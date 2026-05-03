"""Tests for SQL connector base types."""

from __future__ import annotations

import pytest

from hara.connectors.sql.base import (
    ColumnInfo,
    ColumnStatistics,
    ConnectorHealth,
    SQLConnector,
    SQLResult,
    TableInfo,
)


def test_sql_result_has_required_fields() -> None:
    result = SQLResult(columns=["id", "name"], rows=[(1, "alice")], dialect="sqlite")
    assert result.columns == ["id", "name"]
    assert result.rows == [(1, "alice")]
    assert result.dialect == "sqlite"


def test_table_info_with_optional_statistics() -> None:
    table = TableInfo(
        name="users",
        columns=[ColumnInfo(name="id", type="INTEGER")],
        row_count=100,
        column_statistics=None,
    )
    assert table.name == "users"
    assert table.row_count == 100


def test_connector_health_ok() -> None:
    health = ConnectorHealth(ok=True, message="connected")
    assert health.ok is True


def test_sql_connector_is_abstract() -> None:
    assert hasattr(SQLConnector, "__abstractmethods__")
    # Cannot instantiate
    with pytest.raises(TypeError):
        SQLConnector()  # type: ignore[abstract]


def test_column_statistics_defaults_all_none() -> None:
    """All optional fields default to None so the connector can populate
    only what it can compute cheaply."""
    s = ColumnStatistics()
    assert s.row_count is None
    assert s.null_percentage is None
    assert s.min is None
    assert s.max is None
    assert s.mean is None
    assert s.std_dev is None
    assert s.median is None
    assert s.distinct_count is None
    assert s.top_values is None
    assert s.all_unique_values is None


def test_column_statistics_carries_new_fields() -> None:
    s = ColumnStatistics(
        row_count=10,
        null_percentage=20.5,
        min=1.0,
        max=99.0,
        mean=42.0,
        std_dev=12.3,
        median=42.5,
        distinct_count=8,
        top_values=["a", "b"],
        all_unique_values=["a", "b", "c"],
    )
    assert s.null_percentage == 20.5
    assert s.std_dev == 12.3
    assert s.median == 42.5
    assert s.all_unique_values == ["a", "b", "c"]
