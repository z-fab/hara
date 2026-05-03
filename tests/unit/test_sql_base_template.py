"""Tests for SQLConnector's template-method default list_tables."""

from __future__ import annotations

import polars as pl

from hara.config.schemas import SQLConnectorConfigUnion
from hara.connectors.sql.base import (
    ColumnInfo,
    ConnectorHealth,
    SQLConnector,
    SQLResult,
)


class _FakeConnector(SQLConnector):
    """Minimal SQLConnector subclass that only implements the primitives.

    Used to verify the base class's default ``list_tables`` computes stats
    automatically from ``_read_table_dataframe``.
    """

    def __init__(self) -> None:
        self._tables: dict[str, pl.DataFrame] = {
            "producao": pl.DataFrame({"uf": ["MT", "PR", "MT"], "tons": [100, 80, 90]}),
        }

    @classmethod
    def from_config(cls, _config: SQLConnectorConfigUnion) -> _FakeConnector:
        return cls()

    async def execute_query(self, _sql: str, *, read_only: bool = True) -> SQLResult:
        raise NotImplementedError

    async def upsert_table(
        self,
        _table_name: str,
        _df: pl.DataFrame,
        *,
        mode: str = "replace",
    ) -> None:
        raise NotImplementedError

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(ok=True, message="fake")

    @property
    def dialect(self) -> str:
        return "fake"

    # The three primitives — all the implementer needs to provide.

    async def _list_table_names(self) -> list[str]:
        return list(self._tables)

    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        df = self._tables[table_name]
        return [ColumnInfo(name=c, type=str(df[c].dtype)) for c in df.columns]

    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        return self._tables[table_name]


async def test_template_list_tables_returns_table_info() -> None:
    [info] = await _FakeConnector().list_tables()
    assert info.name == "producao"
    assert {c.name for c in info.columns} == {"uf", "tons"}
    assert info.row_count == 3


async def test_template_list_tables_populates_stats_automatically() -> None:
    """The whole point of the template method: implementer doesn't write
    stats logic but TableInfo.column_statistics is populated."""
    [info] = await _FakeConnector().list_tables()
    assert info.column_statistics is not None
    stats = info.column_statistics
    # Numeric column got numeric stats
    assert stats["tons"].min == 80.0
    assert stats["tons"].max == 100.0
    # Text column got categorical stats
    assert stats["uf"].distinct_count == 2
    assert stats["uf"].top_values is not None


async def test_template_list_tables_handles_read_failure_gracefully() -> None:
    """A backend that crashes on _read_table_dataframe still emits the
    table with cols + row_count=0 and no stats."""

    class _BrokenRead(_FakeConnector):
        async def _read_table_dataframe(self, _table_name: str) -> pl.DataFrame:
            raise RuntimeError("backend down")

    [info] = await _BrokenRead().list_tables()
    assert info.name == "producao"
    assert info.row_count == 0
    assert info.column_statistics is None
