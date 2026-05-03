"""CSV → SQL pipeline.

Coordinates: scanner output → CSV parser → connector.upsert_table → record
in ``hara_ingested_files``. Implements the three ``--on-conflict`` modes
defined in §9 of the spec (``skip`` | ``replace`` | ``append``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from hara.connectors.sql.base import SQLConnector
from hara.ingest import OnConflict
from hara.ingest.parsers.csv import parse_csv
from hara.ingest.progress import ProgressCallback, ProgressEvent, noop
from hara.ingest.scanner import ScannedFile
from hara.services.session_store import IngestedFileRecord, SessionStore
from hara.utils.exceptions import IngestError
from hara.utils.identifiers import sha256_file, slugify_relative_path

log = logging.getLogger(__name__)


class StructuredItemStatus(StrEnum):
    OK = "ok"
    SKIPPED_DEDUP = "skipped_dedup"  # exact bytes already ingested
    SKIPPED_TABLE_EXISTS = "skipped_collision"  # different hash but same target table
    FAILED = "failed"


@dataclass(frozen=True)
class StructuredItem:
    relative_path: str
    table_name: str
    status: StructuredItemStatus
    rows: int = 0
    error: str | None = None


@dataclass
class StructuredResult:
    items: list[StructuredItem] = field(default_factory=list[StructuredItem])


async def run_structured_pipeline(
    *,
    files: list[ScannedFile],
    connector: SQLConnector,
    store: SessionStore,
    on_conflict: OnConflict,
    strict: bool = False,
    progress: ProgressCallback = noop,
) -> StructuredResult:
    """Process every ``target='sql'`` file in ``files``.

    Failures on individual files are captured into the per-item ``error``
    field (status = FAILED) and do **not** abort the batch by default. When
    ``strict=True`` the first failure re-raises so the caller (CLI) can
    abort with exit-code 1 — matches §9 of the spec.
    """
    sql_files = [f for f in files if f.target == "sql"]
    out = StructuredResult()
    total = len(sql_files)

    for idx, f in enumerate(sql_files, start=1):
        progress(
            ProgressEvent(
                pipeline="sql",
                file=f.relative_path,
                index=idx,
                total=total,
                stage="start",
            )
        )
        try:
            content_hash = sha256_file(f.absolute_path)
            # Table name derives from the scanner's relative_path so subfolders
            # contribute (2024/dados.csv → 2024_dados, distinct from 2023/dados.csv).
            table_name = slugify_relative_path(Path(f.relative_path))

            existing = await store.find_ingested_file(content_hash=content_hash, target="sql")
            if existing is not None and on_conflict == "skip":
                item = StructuredItem(
                    relative_path=f.relative_path,
                    table_name=table_name,
                    status=StructuredItemStatus.SKIPPED_DEDUP,
                )
                out.items.append(item)
                progress(
                    ProgressEvent(
                        pipeline="sql",
                        file=f.relative_path,
                        index=idx,
                        total=total,
                        stage="skipped",
                        detail=item.status.value,
                    )
                )
                continue

            existing_tables = {t.name for t in await connector.list_tables()}
            same_table_diff_hash = (
                table_name in existing_tables
                and (existing is None)  # different hash, since lookup returned nothing
            )
            if same_table_diff_hash and on_conflict == "skip":
                log.warning(
                    "table %s exists with different hash; use --on-conflict=replace",
                    table_name,
                )
                item = StructuredItem(
                    relative_path=f.relative_path,
                    table_name=table_name,
                    status=StructuredItemStatus.SKIPPED_TABLE_EXISTS,
                )
                out.items.append(item)
                progress(
                    ProgressEvent(
                        pipeline="sql",
                        file=f.relative_path,
                        index=idx,
                        total=total,
                        stage="skipped",
                        detail=item.status.value,
                    )
                )
                continue

            df, rows = parse_csv(f.absolute_path)
            # Mode mapping: append-or-replace; for skip-mode we already short-
            # circuited above when a previous record/table existed, so reaching
            # here means "create or overwrite" — replace handles both.
            mode = "append" if on_conflict == "append" else "replace"

            if on_conflict == "replace":
                # Wipe any prior record of this logical id so the regen snapshot
                # used by `hara semantic-map` doesn't carry stale hashes.
                await store.delete_ingested_files_by_target_id(
                    target="sql", table_or_file_id=table_name
                )
                # Logical-rename case: same bytes (same content_hash) re-ingested
                # under a NEW table name. Delete the row keyed on this hash with
                # its prior table_or_file_id so the snapshot doesn't carry both
                # the old and new logical ids. (The PK is (content_hash, target),
                # so the upsert below would update in place, but we delete
                # explicitly to keep behavior obvious and robust against
                # any future PK changes.)
                if existing is not None and existing.table_or_file_id != table_name:
                    await store.delete_ingested_file(
                        content_hash=existing.content_hash, target="sql"
                    )

            await connector.upsert_table(table_name, df, mode=mode)
            await store.record_ingested_file(
                IngestedFileRecord(
                    content_hash=content_hash,
                    target="sql",
                    relative_path=f.relative_path,
                    table_or_file_id=table_name,
                    rows_count=rows,
                )
            )
            out.items.append(
                StructuredItem(
                    relative_path=f.relative_path,
                    table_name=table_name,
                    status=StructuredItemStatus.OK,
                    rows=rows,
                )
            )
            progress(
                ProgressEvent(
                    pipeline="sql",
                    file=f.relative_path,
                    index=idx,
                    total=total,
                    stage="done",
                    detail=f"{rows} rows",
                )
            )
        except IngestError as e:
            if strict:
                raise
            out.items.append(
                StructuredItem(
                    relative_path=f.relative_path,
                    table_name="",
                    status=StructuredItemStatus.FAILED,
                    error=str(e),
                )
            )
            progress(
                ProgressEvent(
                    pipeline="sql",
                    file=f.relative_path,
                    index=idx,
                    total=total,
                    stage="failed",
                    detail=str(e),
                )
            )
        except Exception as e:  # same-batch isolation
            log.exception("unexpected failure processing %s", f.relative_path)
            if strict:
                raise IngestError(f"strict mode: failed processing {f.relative_path}: {e}") from e
            out.items.append(
                StructuredItem(
                    relative_path=f.relative_path,
                    table_name="",
                    status=StructuredItemStatus.FAILED,
                    error=f"{type(e).__name__}: {e}",
                )
            )
            progress(
                ProgressEvent(
                    pipeline="sql",
                    file=f.relative_path,
                    index=idx,
                    total=total,
                    stage="failed",
                    detail=f"{type(e).__name__}: {e}",
                )
            )

    return out
