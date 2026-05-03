"""High-level orchestrator: scanner -> both pipelines -> merged report."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from hara.connectors.sql.base import SQLConnector
from hara.connectors.vector.base import VectorConnector
from hara.ingest import OnConflict
from hara.ingest.pipelines.structured import (
    StructuredResult,
    run_structured_pipeline,
)
from hara.ingest.pipelines.unstructured import (
    UnstructuredResult,
    run_unstructured_pipeline,
)
from hara.ingest.progress import ProgressCallback, noop
from hara.ingest.scanner import ScannedFile, scan
from hara.services.session_store import SessionStore

TargetFilter = Literal["auto", "sql", "vector"]


@dataclass
class IngestReport:
    """Aggregated result of a full ingest run.

    ``scanned`` counts files dispatched to a pipeline (SQL or vector).
    ``ignored`` counts files the scanner classified as ``ignored`` (unknown
    extension); the value reflects what was on disk, not what was filtered
    out by the user's ``--target`` selection.
    """

    scanned: int
    ignored: int
    structured: StructuredResult
    unstructured: UnstructuredResult


def _filter_by_target(files: list[ScannedFile], target: TargetFilter) -> list[ScannedFile]:
    """Drop files whose target doesn't match the user's ``--target`` filter.

    ``auto`` (default) keeps everything; ``sql``/``vector`` keep only matching
    targets plus ``ignored`` entries (so the scanner's ignored count still
    reflects what was on disk, not what was filtered).
    """
    if target == "auto":
        return files
    return [f for f in files if f.target in (target, "ignored")]


async def run_ingest(
    *,
    root: Path,
    sql_connector: SQLConnector,
    vector_connector: VectorConnector,
    store: SessionStore,
    on_conflict: OnConflict = "skip",
    target: TargetFilter = "auto",
    strict: bool = False,
    chunk_size: int = 1500,
    chunk_overlap: int = 200,
    progress: ProgressCallback = noop,
) -> IngestReport:
    """Walk ``root`` and ingest every recognized file.

    Both pipelines run concurrently — they touch independent resources
    (SQL connector vs. Vector connector + Session Store rows partitioned
    by ``target``), so there is no contention beyond the SQLite session
    DB which serializes writes anyway.

    Args:
        root: Path (file or directory) to ingest.
        sql_connector: Target for ``.csv`` files.
        vector_connector: Target for documents (``.md``, ``.txt``, ``.pdf``,
            ``.docx``).
        store: Session store used for dedup/idempotency bookkeeping.
        on_conflict: ``skip`` (default) | ``replace`` | ``append`` — see §9.
        target: ``auto`` (default) processes both SQL and Vector files;
            ``sql`` skips vector files; ``vector`` skips SQL files.
        strict: When True, ``scan(strict=True)`` raises on unsupported
            extensions AND each pipeline re-raises on the first per-file
            failure (§9 of the spec).
        chunk_size: Max characters per chunk for the unstructured pipeline.
        chunk_overlap: Character overlap between successive chunks.
    """
    files = _filter_by_target(scan(root, strict=strict), target)
    scanned = sum(1 for f in files if f.target != "ignored")
    ignored = sum(1 for f in files if f.target == "ignored")

    structured_task = asyncio.create_task(
        run_structured_pipeline(
            files=files,
            connector=sql_connector,
            store=store,
            on_conflict=on_conflict,
            strict=strict,
            progress=progress,
        )
    )
    unstructured_task = asyncio.create_task(
        run_unstructured_pipeline(
            files=files,
            connector=vector_connector,
            store=store,
            on_conflict=on_conflict,
            strict=strict,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            progress=progress,
        )
    )
    # Cancellation discipline (strict mode + concurrent failures):
    # ``asyncio.gather`` raises as soon as any awaited child raises, but the
    # sibling task keeps running and would continue mutating SQL / vector /
    # session-store state after the caller observed failure. We cancel any
    # still-running sibling and absorb its CancelledError before re-raising,
    # so the invariant holds: when ``run_ingest`` returns or raises, neither
    # pipeline is still running.
    try:
        results = await asyncio.gather(structured_task, unstructured_task)
    except BaseException:
        for t in (structured_task, unstructured_task):
            if not t.done():
                t.cancel()
        for t in (structured_task, unstructured_task):
            # Suppress the sibling's exception (CancelledError or its own
            # failure); only the originally-raised one propagates.
            with contextlib.suppress(BaseException):
                await t
        raise
    structured, unstructured = results

    return IngestReport(
        scanned=scanned,
        ignored=ignored,
        structured=structured,
        unstructured=unstructured,
    )
