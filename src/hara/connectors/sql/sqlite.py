"""File-backed SQLite connector via aiosqlite.

The path is created on first use if it doesn't exist. Uses one connection
per call (short-lived) — simpler than connection pooling for the size of
HARA's typical workload (one user, low concurrency).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import aiosqlite
import polars as pl

from hara.config.schemas import SQLConnectorConfigUnion, SQLiteSQLConfig
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


class SQLiteConnector(SQLConnector):
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config: SQLConnectorConfigUnion) -> SQLiteConnector:
        if not isinstance(config, SQLiteSQLConfig):
            raise TypeError(f"Expected SQLiteSQLConfig, got {type(config).__name__}")
        return cls(path=config.path)

    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        async with aiosqlite.connect(self._path) as conn:
            if read_only:
                await conn.execute("PRAGMA query_only = 1")
            cursor = await conn.execute(sql)
            raw_rows = await cursor.fetchall()
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows: list[tuple[Any, ...]] = [tuple(r) for r in raw_rows]
            return SQLResult(columns=columns, rows=rows, dialect="sqlite")

    async def _list_table_names(self) -> list[str]:
        async with aiosqlite.connect(self._path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            return [str(row[0]) for row in await cursor.fetchall()]

    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        async with aiosqlite.connect(self._path) as conn:
            cursor = await conn.execute(f'PRAGMA table_info("{table_name}")')
            cols_raw = await cursor.fetchall()
            return [ColumnInfo(name=str(r[1]), type=str(r[2])) for r in cols_raw]

    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        """Read via sync sqlite3 in a thread (polars's read_database wants a sync conn)."""
        return await asyncio.to_thread(_read_dataframe_sync, str(self._path), table_name)

    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        async with aiosqlite.connect(self._path) as conn:
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
            col_defs = ", ".join(
                f'"{c}" {_polars_to_sqlite_type(df.schema[c])}' for c in df.columns
            )
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
            async with aiosqlite.connect(self._path) as conn:
                await conn.execute("SELECT 1")
            return ConnectorHealth(ok=True, message=f"sqlite ready at {self._path}")
        except Exception as e:
            return ConnectorHealth(ok=False, message=f"sqlite failed: {e}")

    @property
    def dialect(self) -> str:
        return "sqlite"


def _read_dataframe_sync(db_path: str, table_name: str) -> pl.DataFrame:
    """Sync helper for ``_read_table_dataframe``. Uses stdlib sqlite3 because
    polars's ``read_database`` wants a sync DBAPI connection."""
    conn = sqlite3.connect(db_path)
    try:
        return pl.read_database(  # pyright: ignore[reportUnknownMemberType]
            f'SELECT * FROM "{table_name}"',  # noqa: S608
            connection=conn,
        )
    finally:
        conn.close()
