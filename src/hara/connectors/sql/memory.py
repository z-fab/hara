"""In-memory SQL connector. For tests/dev only.

Implementation note: uses SQLite ':memory:' under the hood. This gives us
real SQL semantics (read-only enforcement, dialect, joins) for free
without maintaining a fake. Each connector instance has its own DB.
"""

from __future__ import annotations

from typing import Any

import aiosqlite
import polars as pl

from hara.config.schemas import MemorySQLConfig, SQLConnectorConfigUnion
from hara.connectors.sql.base import (
    ColumnInfo,
    ConnectorHealth,
    SQLConnector,
    SQLResult,
)


def _polars_to_sqlite_type(dtype: pl.DataType) -> str:
    """Map a Polars dtype to a SQLite column type affinity."""
    if dtype.is_integer():
        return "INTEGER"
    if dtype.is_float():
        return "REAL"
    if dtype == pl.Boolean:
        return "INTEGER"
    return "TEXT"


class MemorySQLConnector(SQLConnector):
    """In-memory SQLite. Lifetime = lifetime of the instance."""

    def __init__(self) -> None:
        # Single shared connection so the in-memory DB persists across calls.
        self._conn: aiosqlite.Connection | None = None

    @classmethod
    def from_config(cls, config: SQLConnectorConfigUnion) -> MemorySQLConnector:
        if not isinstance(config, MemorySQLConfig):
            raise TypeError(f"Expected MemorySQLConfig, got {type(config).__name__}")
        return cls()

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(":memory:")
            await self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        conn = await self._get_conn()
        if read_only:
            await conn.execute("PRAGMA query_only = 1")
        try:
            cursor = await conn.execute(sql)
            raw_rows = await cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows: list[tuple[Any, ...]] = [tuple(r) for r in raw_rows]
            return SQLResult(columns=columns, rows=rows, dialect="sqlite")
        finally:
            if read_only:
                await conn.execute("PRAGMA query_only = 0")

    async def _list_table_names(self) -> list[str]:
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [str(row[0]) for row in await cursor.fetchall()]

    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        conn = await self._get_conn()
        cursor = await conn.execute(f'PRAGMA table_info("{table_name}")')
        cols_raw = await cursor.fetchall()
        return [ColumnInfo(name=str(r[1]), type=str(r[2])) for r in cols_raw]

    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        """Read all rows via the live aiosqlite conn (in-memory DB lives there);
        convert to polars column-by-column to avoid DB-process boundary issues."""
        conn = await self._get_conn()
        cursor = await conn.execute(f'SELECT * FROM "{table_name}"')  # noqa: S608
        col_names = [d[0] for d in cursor.description] if cursor.description else []
        raw_rows = await cursor.fetchall()
        if not col_names:
            return pl.DataFrame()
        data: dict[str, list[Any]] = {n: [] for n in col_names}
        for row in raw_rows:
            for i, n in enumerate(col_names):
                data[n].append(row[i])
        return pl.DataFrame(data)

    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        conn = await self._get_conn()
        if mode == "replace":
            await conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        elif mode == "skip":
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if await cursor.fetchone() is not None:
                return
        # 'append' just inserts; assumes schema compatibility.
        col_defs = ", ".join(f'"{c}" {_polars_to_sqlite_type(df.schema[c])}' for c in df.columns)
        await conn.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})')
        placeholders = ", ".join("?" * len(df.columns))
        rows = list(df.iter_rows())
        await conn.executemany(
            f'INSERT INTO "{table_name}" VALUES ({placeholders})',  # noqa: S608
            rows,
        )
        await conn.commit()

    async def health_check(self) -> ConnectorHealth:
        try:
            conn = await self._get_conn()
            await conn.execute("SELECT 1")
            return ConnectorHealth(ok=True, message="memory SQLite ready")
        except Exception as e:
            return ConnectorHealth(ok=False, message=f"failed: {e}")

    @property
    def dialect(self) -> str:
        return "sqlite"
