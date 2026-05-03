"""Tests for the unstructured ingest pipeline (file → chunks → vector)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.ingest.pipelines.unstructured import (
    UnstructuredItemStatus,
    run_unstructured_pipeline,
)
from hara.ingest.scanner import ScannedFile
from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path / 's.db'}")
    await s.initialize()
    return s


@pytest.fixture
def connector() -> MemoryVectorConnector:
    return MemoryVectorConnector(embedder=FakeEmbedder())


def _scanned(path: Path, rel: str) -> ScannedFile:
    return ScannedFile(absolute_path=path, relative_path=rel, target="vector")


async def test_pipeline_chunks_and_indexes_md(
    tmp_path: Path,
    store: SessionStore,
    connector: MemoryVectorConnector,
) -> None:
    md = tmp_path / "manual.md"
    md.write_text("# Cap 1\n\ntexto do capitulo um.\n")
    res = await run_unstructured_pipeline(
        files=[_scanned(md, "manual.md")],
        connector=connector,
        store=store,
        on_conflict="skip",
        chunk_size=200,
        chunk_overlap=20,
    )
    assert res.items[0].status == UnstructuredItemStatus.OK
    assert res.items[0].chunks >= 1

    docs = await connector.list_documents()
    assert any(d.file_id == "manual" for d in docs)


async def test_pipeline_skip_when_hash_dedup(
    tmp_path: Path,
    store: SessionStore,
    connector: MemoryVectorConnector,
) -> None:
    md = tmp_path / "manual.md"
    md.write_text("# X\nconteudo.\n")
    scanned = [_scanned(md, "manual.md")]
    await run_unstructured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    second = await run_unstructured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    assert second.items[0].status == UnstructuredItemStatus.SKIPPED_DEDUP


async def test_pipeline_replace_clears_old_chunks(
    tmp_path: Path,
    store: SessionStore,
    connector: MemoryVectorConnector,
) -> None:
    md = tmp_path / "x.md"
    md.write_text("# A\nprimeira versao.\n")
    await run_unstructured_pipeline(
        files=[_scanned(md, "x.md")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    md.write_text("# A\nsegunda versao mais longa.\n")
    second = await run_unstructured_pipeline(
        files=[_scanned(md, "x.md")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    assert second.items[0].status == UnstructuredItemStatus.OK
    docs = await connector.list_documents()
    matching = [d for d in docs if d.file_id == "x"]
    assert len(matching) == 1


async def test_pipeline_append_is_error(
    tmp_path: Path,
    store: SessionStore,
    connector: MemoryVectorConnector,
) -> None:
    md = tmp_path / "a.md"
    md.write_text("conteudo")
    res = await run_unstructured_pipeline(
        files=[_scanned(md, "a.md")],
        connector=connector,
        store=store,
        on_conflict="append",
    )
    assert res.items[0].status == UnstructuredItemStatus.FAILED
    assert "append" in (res.items[0].error or "").lower()
