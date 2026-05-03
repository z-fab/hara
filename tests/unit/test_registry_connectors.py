"""Tests for connector registry (entry-points + programmatic)."""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from hara.connectors import (
    list_sql_connectors,
    list_vector_connectors,
    register_sql_connector,
    resolve_sql_connector_class,
)
from hara.connectors.sql.base import (
    ConnectorHealth,
    SQLConnector,
    SQLResult,
    TableInfo,
)


class _FakeSQLConnector(SQLConnector):
    @classmethod
    def from_config(cls, config: Any) -> _FakeSQLConnector:
        return cls()

    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        return SQLResult(columns=[], rows=[], dialect="fake")

    async def list_tables(self) -> list[TableInfo]:
        return []

    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        return None

    async def health_check(self) -> ConnectorHealth:
        return ConnectorHealth(ok=True, message="fake")

    @property
    def dialect(self) -> str:
        return "fake"


def test_register_and_resolve_sql_programmatic() -> None:
    register_sql_connector("fake", _FakeSQLConnector)
    cls = resolve_sql_connector_class("fake")
    assert cls is _FakeSQLConnector


def test_resolve_unknown_sql_raises() -> None:
    with pytest.raises(KeyError, match="Unknown SQL connector"):
        resolve_sql_connector_class("nonexistent_connector_zzz")


def test_list_sql_includes_programmatic() -> None:
    register_sql_connector("fake_listed", _FakeSQLConnector)
    names = list_sql_connectors()
    assert "fake_listed" in names


def test_list_vector_returns_set() -> None:
    names = list_vector_connectors()
    # Should at least be a set type — entry-points may or may not load until
    # impl modules exist (Tasks 12-17).
    assert isinstance(names, set)
