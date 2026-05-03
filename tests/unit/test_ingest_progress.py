"""Tests for the ingest progress callback infrastructure."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.ingest.pipelines.structured import run_structured_pipeline
from hara.ingest.pipelines.unstructured import run_unstructured_pipeline
from hara.ingest.progress import ProgressEvent
from hara.ingest.scanner import ScannedFile
from hara.services.session_store import SessionStore


@pytest.fixture
async def session_store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path / 'sess.db'}")
    await s.initialize()
    return s


async def test_structured_pipeline_emits_progress(
    session_store: SessionStore, tmp_path: Path
) -> None:
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n")
    files = [
        ScannedFile(
            absolute_path=csv_path,
            relative_path="t.csv",
            target="sql",
        )
    ]
    events: list[ProgressEvent] = []
    conn = MemorySQLConnector()
    await run_structured_pipeline(
        files=files,
        connector=conn,
        store=session_store,
        on_conflict="skip",
        progress=events.append,
    )
    stages = [e.stage for e in events]
    assert "start" in stages
    assert "done" in stages
    # Each event references the same file
    assert all(e.file == "t.csv" for e in events)
    # All structured events have pipeline=sql
    assert all(e.pipeline == "sql" for e in events)
    # done event carries row-count detail
    done = next(e for e in events if e.stage == "done")
    assert "rows" in done.detail


async def test_unstructured_pipeline_emits_progress_with_stages(
    session_store: SessionStore, tmp_path: Path
) -> None:
    md_path = tmp_path / "doc.md"
    md_path.write_text("# Heading\n\nbody.\n")
    files = [
        ScannedFile(
            absolute_path=md_path,
            relative_path="doc.md",
            target="vector",
        )
    ]
    events: list[ProgressEvent] = []
    emb = FakeEmbedder()
    vec = MemoryVectorConnector(embedder=emb)
    await run_unstructured_pipeline(
        files=files,
        connector=vec,
        store=session_store,
        on_conflict="skip",
        progress=events.append,
    )
    stages = [e.stage for e in events]
    assert "start" in stages
    assert "parsing" in stages
    assert "chunking" in stages
    assert "upserting" in stages
    assert "done" in stages
    # 1-based index, total reflects 1 vector file
    assert all(e.index == 1 and e.total == 1 for e in events)
    assert all(e.pipeline == "vector" for e in events)
