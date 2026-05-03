"""Tests for the ingest service orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.ingest import service as service_mod
from hara.ingest.pipelines.structured import StructuredResult
from hara.ingest.pipelines.unstructured import UnstructuredResult
from hara.ingest.service import IngestReport, run_ingest
from hara.services.session_store import SessionStore
from hara.utils.exceptions import IngestError


@pytest.fixture
async def store(tmp_path_factory: pytest.TempPathFactory) -> SessionStore:
    # Keep the session DB in a sibling dir so it doesn't get scanned as a
    # source file when tests pass the test's ``tmp_path`` as the ingest root.
    db_dir = tmp_path_factory.mktemp("session-db")
    s = SessionStore(dsn=f"sqlite:///{db_dir / 'a.db'}")
    await s.initialize()
    return s


async def test_run_ingest_processes_csv_and_md(tmp_path: Path, store: SessionStore) -> None:
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    (tmp_path / "doc.md").write_text("# Title\nbody.\n")

    sql = MemorySQLConnector()
    vec = MemoryVectorConnector(embedder=FakeEmbedder())

    report = await run_ingest(
        root=tmp_path,
        sql_connector=sql,
        vector_connector=vec,
        store=store,
        on_conflict="skip",
        chunk_size=200,
        chunk_overlap=20,
    )
    assert isinstance(report, IngestReport)
    assert len(report.structured.items) == 1
    assert len(report.unstructured.items) == 1
    assert report.scanned == 2
    assert report.ignored == 0


async def test_run_ingest_counts_ignored(tmp_path: Path, store: SessionStore) -> None:
    (tmp_path / "weird.zzz").write_text("x")
    sql = MemorySQLConnector()
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    r = await run_ingest(
        root=tmp_path,
        sql_connector=sql,
        vector_connector=vec,
        store=store,
        on_conflict="skip",
    )
    assert r.ignored == 1
    assert r.scanned == 0


async def test_run_ingest_target_sql_skips_vector_files(
    tmp_path: Path, store: SessionStore
) -> None:
    (tmp_path / "data.csv").write_text("a\n1\n")
    (tmp_path / "doc.md").write_text("texto.\n")
    sql = MemorySQLConnector()
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    r = await run_ingest(
        root=tmp_path,
        sql_connector=sql,
        vector_connector=vec,
        store=store,
        on_conflict="skip",
        target="sql",
    )
    assert len(r.structured.items) == 1
    assert len(r.unstructured.items) == 0


async def test_run_ingest_strict_raises_on_parser_failure(
    tmp_path: Path, store: SessionStore
) -> None:
    """In strict mode, a parser/pipeline failure aborts the whole batch (§9)."""
    (tmp_path / "broken.csv").write_text("a,b\n1,2\n3\n")  # ragged
    sql = MemorySQLConnector()
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    with pytest.raises(IngestError):
        await run_ingest(
            root=tmp_path,
            sql_connector=sql,
            vector_connector=vec,
            store=store,
            on_conflict="skip",
            strict=True,
        )


async def test_run_ingest_strict_cancels_sibling_when_one_pipeline_raises(
    tmp_path: Path,
    store: SessionStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: in strict mode, when one pipeline raises while the
    sibling is still running, the sibling must be cancelled before
    ``run_ingest`` returns/raises — otherwise it keeps mutating state
    after the caller already observed failure.
    """
    (tmp_path / "data.csv").write_text("a,b\n1,2\n")
    (tmp_path / "doc.md").write_text("# T\nbody\n")

    sibling_completed = False
    sibling_cancelled = False

    async def slow_unstructured(*args: object, **kwargs: object) -> UnstructuredResult:
        nonlocal sibling_completed, sibling_cancelled
        try:
            # Long enough that the structured task should raise first.
            await asyncio.sleep(5)
            sibling_completed = True
            return UnstructuredResult()
        except asyncio.CancelledError:
            sibling_cancelled = True
            raise

    async def fast_failing_structured(*args: object, **kwargs: object) -> StructuredResult:
        # Yield once to let the sibling actually start, then blow up.
        await asyncio.sleep(0)
        raise IngestError("boom in strict mode")

    monkeypatch.setattr(service_mod, "run_structured_pipeline", fast_failing_structured)
    monkeypatch.setattr(service_mod, "run_unstructured_pipeline", slow_unstructured)

    sql = MemorySQLConnector()
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    with pytest.raises(IngestError):
        await run_ingest(
            root=tmp_path,
            sql_connector=sql,
            vector_connector=vec,
            store=store,
            on_conflict="skip",
            strict=True,
        )
    # Invariant: when run_ingest raises, the sibling task is no longer
    # running. It must have been cancelled, not completed.
    assert sibling_cancelled, "sibling task should have been cancelled"
    assert not sibling_completed, "sibling task should not have completed"
