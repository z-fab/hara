"""SQL connector contract — abstract base + types.

All SQL connectors implement this protocol. The agent's SQL Executor node
calls `execute_query` for read-only LLM-generated queries. The Ingest
service uses `upsert_table`. The Semantic Map service uses `list_tables`.

To add a new connector type (MySQL, etc.), subclass SQLConnector, implement
all abstract methods, and register via entry-point or programmatically.
See docs/extending/connectors.md for the walkthrough.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import polars as pl

from hara.config.schemas import SQLConnectorConfigUnion


@dataclass(frozen=True)
class SQLResult:
    columns: list[str]
    rows: list[tuple[Any, ...]]
    dialect: str


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    description: str | None = None


@dataclass(frozen=True)
class ColumnStatistics:
    """Optional cheap-to-compute column stats. Populated by connectors that can."""

    row_count: int | None = None
    null_percentage: float | None = None  # 0.0 to 100.0
    min: Any | None = None
    max: Any | None = None
    mean: float | None = None
    std_dev: float | None = None
    median: float | None = None
    distinct_count: int | None = None
    top_values: list[Any] | None = None  # top-10 most frequent
    all_unique_values: list[Any] | None = None  # only when distinct_count <= 20


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    row_count: int | None = None
    column_statistics: dict[str, ColumnStatistics] | None = None


@dataclass(frozen=True)
class ConnectorHealth:
    ok: bool
    message: str
    details: dict[str, Any] | None = None


class SQLConnector(ABC):
    """Abstract SQL connector.

    Implementations must be async-first. Use aiosqlite, asyncpg, or
    similar libraries. Sync libraries should be wrapped via
    `asyncio.to_thread` if no async equivalent exists.

    The default ``list_tables`` is a *template method*: it calls three
    primitives the implementer provides (``_list_table_names``,
    ``_get_table_columns``, ``_read_table_dataframe``) and assembles the
    full :class:`TableInfo` including per-column statistics for free
    via :func:`hara.connectors.sql._stats.calculate_column_stats`.

    A connector with a faster native stats path (Postgres ``pg_stats``
    sample, very large tables, etc.) MAY override ``list_tables``
    directly. In that case it should still populate
    ``column_statistics`` to keep parity with the contract test
    ``test_list_tables_emits_column_statistics``.
    """

    @classmethod
    @abstractmethod
    def from_config(cls, config: SQLConnectorConfigUnion) -> SQLConnector:
        """Construct a connector from the validated config block."""

    @abstractmethod
    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        """Run a SELECT query.

        Args:
            sql: Single-statement SELECT (validated by caller via sqlglot).
            read_only: If True, the connection MUST enforce read-only at
                the transaction level (Postgres: BEGIN READ ONLY; SQLite:
                PRAGMA query_only = 1).

        Raises:
            ConnectorError: On connection or execution failure.
        """

    @abstractmethod
    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        """Create/replace/append a table from a DataFrame.

        Args:
            table_name: Target table.
            df: Polars DataFrame.
            mode: 'replace' (drop+create) | 'append' (insert into existing) |
                'skip' (no-op if exists).
        """

    @abstractmethod
    async def health_check(self) -> ConnectorHealth:
        """Cheap connectivity check. Used by `hara doctor`."""

    @property
    @abstractmethod
    def dialect(self) -> str:
        """SQL dialect identifier ('sqlite' | 'postgres' | ...).

        Used by the SQL Executor node to validate generated SQL via sqlglot.
        """

    # ---------- Template-method primitives ----------

    @abstractmethod
    async def _list_table_names(self) -> list[str]:
        """Return user-visible table names (no system tables).

        Implementer should filter out internal / sqlite_*-style entries.
        """

    @abstractmethod
    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        """Return column metadata (name + native type) for ``table_name``."""

    @abstractmethod
    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        """Read all rows of ``table_name`` as a polars DataFrame.

        Used by the default ``list_tables`` to compute row count + per-column
        statistics. For very large tables a sampling approach is preferable;
        v0.1 connectors load the full table (CSV-scale workload). v0.2 may
        introduce a `_sample_table_dataframe` extension point.
        """

    # ---------- Default list_tables (template method) ----------

    async def list_tables(self) -> list[TableInfo]:
        """Default implementation built on the three primitives above.

        Connectors that need a faster native path may override this method
        entirely; otherwise just implement the primitives and statistics
        come for free via :func:`calculate_column_stats`.
        """
        # Lazy import to keep the base file free of pl-stats dependencies at parse time.
        from hara.connectors.sql._stats import calculate_column_stats  # noqa: PLC0415

        names = await self._list_table_names()
        result: list[TableInfo] = []
        for name in names:
            cols = await self._get_table_columns(name)
            try:
                df = await self._read_table_dataframe(name)
            except Exception:
                # Backend failed to read this table; emit minimal info so
                # the rest of list_tables doesn't blow up.
                result.append(TableInfo(name=name, columns=cols, row_count=0))
                continue

            row_count = df.height
            allowed = {c.name for c in cols}
            stats: dict[str, ColumnStatistics] = {}
            for col in df.columns:
                if col not in allowed:
                    continue
                try:
                    stats[col] = calculate_column_stats(df[col])
                except Exception:  # noqa: S112 — per-column failure: skip, keep the rest.
                    continue

            result.append(
                TableInfo(
                    name=name,
                    columns=cols,
                    row_count=row_count,
                    column_statistics=stats or None,
                )
            )
        return result
