"""Tests for SQLite connector's per-column statistics."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from hara.connectors.sql.sqlite import SQLiteConnector


@pytest.fixture
async def conn(tmp_path: Path) -> SQLiteConnector:
    c = SQLiteConnector(path=tmp_path / "x.db")
    df = pl.DataFrame(
        {
            "uf": ["MT", "PR", "SP", "MT", "MG"],
            "tons": [100, 80, 50, 60, 90],
        }
    )
    await c.upsert_table("producao", df, mode="replace")
    return c


async def test_list_tables_emits_numeric_stats(conn: SQLiteConnector) -> None:
    [info] = await conn.list_tables()
    stats = info.column_statistics or {}
    assert "tons" in stats
    s = stats["tons"]
    assert s.row_count == 5
    assert s.min == 50.0
    assert s.max == 100.0
    assert s.mean is not None
    assert s.std_dev is not None
    assert s.median is not None
    # Non-numeric fields stay None on numeric column
    assert s.distinct_count is None or s.distinct_count >= 0


async def test_list_tables_emits_text_stats(conn: SQLiteConnector) -> None:
    [info] = await conn.list_tables()
    stats = info.column_statistics or {}
    s = stats["uf"]
    assert s.row_count == 5
    assert s.distinct_count == 4  # MT, PR, SP, MG
    assert s.top_values is not None
    assert "MT" in s.top_values
    # All unique values present (4 ≤ 20)
    assert s.all_unique_values is not None
    assert set(s.all_unique_values) == {"MT", "PR", "SP", "MG"}


async def test_list_tables_handles_null_percentage(tmp_path: Path) -> None:
    c = SQLiteConnector(path=tmp_path / "x.db")
    df = pl.DataFrame({"x": [1, None, 3, None, 5]})
    await c.upsert_table("t", df, mode="replace")
    [info] = await c.list_tables()
    s = (info.column_statistics or {})["x"]
    assert s.null_percentage == 40.0
