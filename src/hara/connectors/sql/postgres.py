"""Postgres connector via asyncpg.

Uses connection-per-call (short-lived). For higher load, swap to a pool;
for v0.1's expected workload (single self-hosted instance), simple is enough.
"""

from __future__ import annotations

from typing import Any

import asyncpg  # pyright: ignore[reportMissingTypeStubs]
import polars as pl

from hara.config.schemas import PostgresSQLConfig, SQLConnectorConfigUnion
from hara.connectors.sql.base import (
    ColumnInfo,
    ConnectorHealth,
    SQLConnector,
    SQLResult,
)


class PostgresConnector(SQLConnector):
    def __init__(self, url: str) -> None:
        self._url = url

    @classmethod
    def from_config(cls, config: SQLConnectorConfigUnion) -> PostgresConnector:
        if not isinstance(config, PostgresSQLConfig):
            raise TypeError(f"Expected PostgresSQLConfig, got {type(config).__name__}")
        return cls(url=config.url.get_secret_value())

    async def _connect(self) -> Any:
        return await asyncpg.connect(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            self._url
        )

    async def execute_query(self, sql: str, *, read_only: bool = True) -> SQLResult:
        conn: Any = await self._connect()
        try:
            # Prepare introspects column metadata up front, so we still know the
            # schema even when the query returns zero rows.
            if read_only:
                await conn.execute("BEGIN READ ONLY")
                try:
                    stmt: Any = await conn.prepare(sql)
                    columns: list[str] = [a.name for a in stmt.get_attributes()]
                    records: list[Any] = await stmt.fetch()
                finally:
                    await conn.execute("COMMIT")
            else:
                stmt = await conn.prepare(sql)
                columns = [a.name for a in stmt.get_attributes()]
                records = await stmt.fetch()

            rows: list[tuple[Any, ...]] = [tuple(r.values()) for r in records]
            return SQLResult(columns=columns, rows=rows, dialect="postgres")
        finally:
            await conn.close()

    async def _list_table_names(self) -> list[str]:
        conn: Any = await self._connect()
        try:
            rows: list[Any] = await conn.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public'"
            )
            return [str(r["tablename"]) for r in rows]
        finally:
            await conn.close()

    async def _get_table_columns(self, table_name: str) -> list[ColumnInfo]:
        conn: Any = await self._connect()
        try:
            rows: list[Any] = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=$1",
                table_name,
            )
            return [ColumnInfo(name=str(r["column_name"]), type=str(r["data_type"])) for r in rows]
        finally:
            await conn.close()

    async def _read_table_dataframe(self, table_name: str) -> pl.DataFrame:
        conn: Any = await self._connect()
        try:
            records: list[Any] = await conn.fetch(
                f'SELECT * FROM "{table_name}"'  # noqa: S608 — table_name from pg_tables
            )
        finally:
            await conn.close()
        if not records:
            return pl.DataFrame()
        return pl.from_dicts([dict(r) for r in records])

    async def upsert_table(
        self, table_name: str, df: pl.DataFrame, *, mode: str = "replace"
    ) -> None:
        conn: Any = await self._connect()
        try:
            if mode == "replace":
                await conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            elif mode == "skip":
                exists: Any = await conn.fetchval(
                    "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=$1",
                    table_name,
                )
                if exists:
                    return
            col_defs = ", ".join(
                f'"{name}" {_polars_to_postgres_type(dtype)}'
                for name, dtype in zip(df.columns, df.dtypes, strict=True)
            )
            await conn.execute(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({col_defs})')
            rows: list[tuple[Any, ...]] = [tuple(r) for r in df.iter_rows()]
            await conn.copy_records_to_table(table_name, records=rows)
        finally:
            await conn.close()

    async def health_check(self) -> ConnectorHealth:
        try:
            conn: Any = await self._connect()
            try:
                await conn.fetchval("SELECT 1")
                return ConnectorHealth(ok=True, message="postgres ready")
            finally:
                await conn.close()
        except Exception as e:
            return ConnectorHealth(ok=False, message=f"postgres failed: {e}")

    @property
    def dialect(self) -> str:
        return "postgres"


def _polars_to_postgres_type(dtype: pl.DataType) -> str:
    """Map a polars dtype to a Postgres column type. Defaults to TEXT for unknown."""
    if dtype.is_integer():
        return "BIGINT"
    if dtype.is_float():
        return "DOUBLE PRECISION"
    if dtype == pl.Boolean:
        return "BOOLEAN"
    if dtype == pl.Date:
        return "DATE"
    if dtype == pl.Datetime:
        return "TIMESTAMP"
    return "TEXT"
