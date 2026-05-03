"""Parser → chunk → embed → vector pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from hara.connectors.vector.base import VectorConnector
from hara.ingest import OnConflict
from hara.ingest.chunking import chunk_text
from hara.ingest.parsers.text import parse_text
from hara.ingest.progress import ProgressCallback, ProgressEvent, Stage, noop
from hara.ingest.scanner import ScannedFile
from hara.services.session_store import IngestedFileRecord, SessionStore
from hara.utils.exceptions import IngestError
from hara.utils.identifiers import sha256_file, slugify_relative_path

log = logging.getLogger(__name__)

# The connector embeds chunks itself (configured at construction time via
# from_config). Actual batching is internal to the connector implementation.


class UnstructuredItemStatus(StrEnum):
    OK = "ok"
    SKIPPED_DEDUP = "skipped_dedup"
    SKIPPED_FILE_EXISTS = "skipped_collision"
    FAILED = "failed"


@dataclass(frozen=True)
class UnstructuredItem:
    relative_path: str
    file_id: str
    status: UnstructuredItemStatus
    chunks: int = 0
    tokens: int | None = None
    error: str | None = None


@dataclass
class UnstructuredResult:
    items: list[UnstructuredItem] = field(default_factory=list[UnstructuredItem])


def _select_parser(path: Path) -> Callable[[Path], tuple[str, int]]:
    ext = path.suffix.lower()
    if ext in {".md", ".txt"}:
        return parse_text
    if ext == ".pdf":
        from hara.ingest.parsers.pdf import parse_pdf  # noqa: PLC0415 — optional dep, lazy load

        return parse_pdf
    if ext == ".docx":
        from hara.ingest.parsers.docx import parse_docx  # noqa: PLC0415 — optional dep, lazy load

        return parse_docx
    raise IngestError(f"no unstructured parser for extension {ext!r}")


async def run_unstructured_pipeline(  # noqa: PLR0915 — progress wiring + per-file error branches expand statement count
    *,
    files: list[ScannedFile],
    connector: VectorConnector,
    store: SessionStore,
    on_conflict: OnConflict,
    strict: bool = False,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    progress: ProgressCallback = noop,
) -> UnstructuredResult:
    """Process every ``target='vector'`` file in ``files``.

    The connector already carries its embedder (injected via
    ``from_config``), so embeddings are produced inside ``upsert_chunks``.

    ``--on-conflict='append'`` is rejected outright per §9: append in a
    vector store would silently duplicate chunks. Users wanting to add
    must rename the file (different ``file_id``) or use ``replace``.

    ``strict=True`` re-raises on the first per-file failure; default is
    batch-isolated (per-item ``status=FAILED``).
    """
    vec_files = [f for f in files if f.target == "vector"]
    out = UnstructuredResult()
    total = len(vec_files)

    def _emit(rel: str, i: int, stage: Stage, detail: str = "") -> None:
        progress(
            ProgressEvent(
                pipeline="vector",
                file=rel,
                index=i,
                total=total,
                stage=stage,
                detail=detail,
            )
        )

    for idx, f in enumerate(vec_files, start=1):
        rel = f.relative_path
        _emit(rel, idx, "start")
        try:
            if on_conflict == "append":
                raise IngestError(
                    "--on-conflict=append is not supported for vector targets; "
                    "use 'replace' or rename the file"
                )

            content_hash = sha256_file(f.absolute_path)
            file_id = slugify_relative_path(Path(f.relative_path))

            existing = await store.find_ingested_file(content_hash=content_hash, target="vector")
            if existing is not None and on_conflict == "skip":
                item = UnstructuredItem(
                    relative_path=f.relative_path,
                    file_id=file_id,
                    status=UnstructuredItemStatus.SKIPPED_DEDUP,
                )
                out.items.append(item)
                _emit(rel, idx, "skipped", item.status.value)
                continue

            existing_doc_ids = {d.file_id for d in await connector.list_documents()}
            if file_id in existing_doc_ids and on_conflict == "skip":
                log.warning(
                    "vector doc %s exists with different hash; use --on-conflict=replace",
                    file_id,
                )
                item = UnstructuredItem(
                    relative_path=f.relative_path,
                    file_id=file_id,
                    status=UnstructuredItemStatus.SKIPPED_FILE_EXISTS,
                )
                out.items.append(item)
                _emit(rel, idx, "skipped", item.status.value)
                continue

            parser = _select_parser(f.absolute_path)
            _emit(rel, idx, "parsing")
            text, _ = parser(f.absolute_path)
            _emit(rel, idx, "chunking")
            chunks = chunk_text(
                text,
                file_id=file_id,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            if not chunks:
                raise IngestError(f"document produced 0 chunks: {f.relative_path}")

            if on_conflict == "replace":
                if file_id in existing_doc_ids:
                    await connector.delete_document(file_id)
                # Wipe stale ingested_files rows referencing the same logical
                # id (different content_hash) so the regen snapshot is clean.
                await store.delete_ingested_files_by_target_id(
                    target="vector", table_or_file_id=file_id
                )
                # Logical-rename case: same bytes (same content_hash) re-ingested
                # under a NEW file_id. Delete the row keyed on this hash with
                # its prior table_or_file_id so the snapshot doesn't carry both
                # the old and new logical ids.
                if existing is not None and existing.table_or_file_id != file_id:
                    await store.delete_ingested_file(
                        content_hash=existing.content_hash, target="vector"
                    )

            _emit(rel, idx, "upserting")
            await connector.upsert_chunks(chunks)
            tokens = _estimate_tokens(text)

            await store.record_ingested_file(
                IngestedFileRecord(
                    content_hash=content_hash,
                    target="vector",
                    relative_path=f.relative_path,
                    table_or_file_id=file_id,
                    chunks_count=len(chunks),
                    tokens_used=tokens,
                )
            )
            out.items.append(
                UnstructuredItem(
                    relative_path=f.relative_path,
                    file_id=file_id,
                    status=UnstructuredItemStatus.OK,
                    chunks=len(chunks),
                    tokens=tokens,
                )
            )
            _emit(rel, idx, "done", f"{len(chunks)} chunks")
        except IngestError as e:
            if strict:
                raise
            out.items.append(
                UnstructuredItem(
                    relative_path=f.relative_path,
                    file_id="",
                    status=UnstructuredItemStatus.FAILED,
                    error=str(e),
                )
            )
            _emit(rel, idx, "failed", str(e))
        except Exception as e:  # same-batch isolation: capture, don't propagate
            log.exception("unexpected failure processing %s", f.relative_path)
            if strict:
                raise IngestError(f"strict mode: failed processing {f.relative_path}: {e}") from e
            out.items.append(
                UnstructuredItem(
                    relative_path=f.relative_path,
                    file_id="",
                    status=UnstructuredItemStatus.FAILED,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            _emit(rel, idx, "failed", f"{type(e).__name__}: {e}")

    return out


def _estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4).

    Good enough for telemetry; the real number is what the embeddings
    provider consumes.
    """
    return max(1, len(text) // 4)
