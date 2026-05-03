"""Tests for the CSV → SQL ingest pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.connectors.sql.memory import MemorySQLConnector
from hara.ingest.pipelines.structured import (
    StructuredItemStatus,
    StructuredResult,
    run_structured_pipeline,
)
from hara.ingest.scanner import ScannedFile
from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path / 's.db'}")
    await s.initialize()
    return s


@pytest.fixture
def connector() -> MemorySQLConnector:
    return MemorySQLConnector()


def _scanned(path: Path, rel: str) -> ScannedFile:
    return ScannedFile(absolute_path=path, relative_path=rel, target="sql")


async def test_pipeline_inserts_csv_into_table(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    csv = tmp_path / "dados.csv"
    csv.write_text("a,b\n1,2\n3,4\n")
    scanned = [_scanned(csv, "dados.csv")]

    result = await run_structured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    assert isinstance(result, StructuredResult)
    assert len(result.items) == 1
    assert result.items[0].status == StructuredItemStatus.OK
    assert result.items[0].table_name == "dados"
    assert result.items[0].rows == 2

    tables = {t.name for t in await connector.list_tables()}
    assert "dados" in tables


async def test_pipeline_skip_when_hash_already_recorded(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    csv = tmp_path / "dados.csv"
    csv.write_text("a,b\n1,2\n")
    scanned = [_scanned(csv, "dados.csv")]

    await run_structured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    # Second run with same content: should skip (no-op).
    second = await run_structured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    assert second.items[0].status == StructuredItemStatus.SKIPPED_DEDUP


async def test_pipeline_skip_when_table_exists_with_different_hash(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    csv = tmp_path / "dados.csv"
    csv.write_text("a,b\n1,2\n")
    await run_structured_pipeline(
        files=[_scanned(csv, "dados.csv")],
        connector=connector,
        store=store,
        on_conflict="skip",
    )
    # Mutate file content
    csv.write_text("a,b\n9,8\n")
    second = await run_structured_pipeline(
        files=[_scanned(csv, "dados.csv")],
        connector=connector,
        store=store,
        on_conflict="skip",
    )
    assert second.items[0].status == StructuredItemStatus.SKIPPED_TABLE_EXISTS
    # Old data still in the table
    res = await connector.execute_query("SELECT a FROM dados ORDER BY a")
    assert [r[0] for r in res.rows] == [1]


async def test_pipeline_replace_drops_and_recreates(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    csv = tmp_path / "dados.csv"
    csv.write_text("a,b\n1,2\n")
    await run_structured_pipeline(
        files=[_scanned(csv, "dados.csv")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    csv.write_text("a,b\n9,8\n")
    second = await run_structured_pipeline(
        files=[_scanned(csv, "dados.csv")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    assert second.items[0].status == StructuredItemStatus.OK
    res = await connector.execute_query("SELECT a FROM dados")
    assert {r[0] for r in res.rows} == {9}


async def test_pipeline_replace_rename_drops_old_logical_id_row(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    """Rename case: identical bytes moved to a new path → new ``table_or_file_id``.

    The pipeline's ``replace`` branch must explicitly delete the stale
    row keyed on ``content_hash`` (with its prior ``table_or_file_id``)
    before re-recording, so the snapshot used by ``hara semantic-map``
    contains only the new logical id.

    This test pre-seeds a stale row with the same content_hash but a
    *different* relative_path/table_or_file_id to simulate the post-rename
    state and asserts the pipeline cleans it up.
    """
    a = tmp_path / "a.csv"
    a.write_text("a,b\n1,2\n")
    await run_structured_pipeline(
        files=[_scanned(a, "a.csv")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    # Move bytes: delete a.csv, write same content to b.csv.
    a.unlink()
    b = tmp_path / "b.csv"
    b.write_text("a,b\n1,2\n")
    await run_structured_pipeline(
        files=[_scanned(b, "b.csv")],
        connector=connector,
        store=store,
        on_conflict="replace",
    )
    rows = await store.list_ingested_files(target="sql")
    assert len(rows) == 1, [r.relative_path for r in rows]
    assert rows[0].table_or_file_id == "b"
    assert rows[0].relative_path == "b.csv"


async def test_pipeline_failure_does_not_abort_batch(
    tmp_path: Path, store: SessionStore, connector: MemorySQLConnector
) -> None:
    good = tmp_path / "good.csv"
    good.write_text("a\n1\n")
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n3\n")  # ragged
    scanned = [_scanned(good, "good.csv"), _scanned(bad, "bad.csv")]

    result = await run_structured_pipeline(
        files=scanned, connector=connector, store=store, on_conflict="skip"
    )
    statuses = [i.status for i in result.items]
    assert StructuredItemStatus.OK in statuses
    assert StructuredItemStatus.FAILED in statuses
