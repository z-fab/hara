"""Session Store: persists threads, turns, events, and ingested files.

Schema is idempotent (CREATE TABLE IF NOT EXISTS) and runs at startup of any
HARA process (`serve`, `chat`, `ingest`). v0.1 supports SQLite and Postgres
via async drivers (aiosqlite, asyncpg). Migrations (Alembic) are reserved
for v0.2 when schema changes break compatibility.
"""

from __future__ import annotations

import json
import secrets
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import aiosqlite
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from hara.config.settings import Settings

# Schema is dialect-portable enough to share between SQLite and Postgres for v0.1.
# Differences (timestamp types, autoincrement) are handled per-dialect at execute time.
_SCHEMA_SQLITE = [
    """
    CREATE TABLE IF NOT EXISTS hara_threads (
        thread_id TEXT PRIMARY KEY,
        title TEXT,
        metadata TEXT,
        created_at TIMESTAMP NOT NULL,
        last_active_at TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hara_turns (
        turn_id TEXT PRIMARY KEY,
        thread_id TEXT NOT NULL REFERENCES hara_threads(thread_id) ON DELETE CASCADE,
        status TEXT NOT NULL,                -- running | completed | failed | canceled
        question TEXT NOT NULL,
        answer TEXT,
        citations TEXT,                       -- JSON
        metadata TEXT,                        -- JSON
        started_at TIMESTAMP NOT NULL,
        completed_at TIMESTAMP,
        canceled_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hara_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        turn_id TEXT NOT NULL REFERENCES hara_turns(turn_id) ON DELETE CASCADE,
        event_type TEXT NOT NULL,             -- state | token | final | done
        data TEXT NOT NULL,                   -- JSON
        ts TIMESTAMP NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS hara_ingested_files (
        content_hash TEXT NOT NULL,
        target TEXT NOT NULL,                 -- sql | vector
        relative_path TEXT NOT NULL,
        table_or_file_id TEXT NOT NULL,
        ingested_at TIMESTAMP NOT NULL,
        chunks_count INTEGER,
        rows_count INTEGER,
        tokens_used INTEGER,
        PRIMARY KEY (content_hash, target)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_hara_turns_thread ON hara_turns(thread_id)",
    "CREATE INDEX IF NOT EXISTS idx_hara_events_turn ON hara_events(turn_id, seq)",
    "CREATE INDEX IF NOT EXISTS idx_hara_ingested_target_id "
    "ON hara_ingested_files(target, table_or_file_id)",
]


class IngestedFileRecord(BaseModel):
    """Row of `hara_ingested_files`.

    Mirrors the table schema. Uses Pydantic so the boundary with the rest of
    HARA's typed code stays clean (the SessionStore module otherwise speaks
    `dict[str, Any]` for thread/turn rows; a Pydantic model is justified
    here because ingest code passes these around as first-class values).
    """

    content_hash: str
    target: Literal["sql", "vector"]
    relative_path: str
    table_or_file_id: str
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    chunks_count: int | None = None
    rows_count: int | None = None
    tokens_used: int | None = None


class SessionStore:
    """Async wrapper around the session DB.

    For v0.1 only the SQLite implementation is included; Postgres support
    is added when the postgres connector lands. The DSN scheme picks the
    driver: `sqlite:///path` -> aiosqlite.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn.startswith("sqlite://"):
            raise NotImplementedError(f"Only sqlite:// DSN supported in this task; got {dsn!r}")
        # sqlite:///./data/hara.db -> ./data/hara.db
        self._db_path = dsn.removeprefix("sqlite:///")

    async def initialize(self) -> None:
        """Create all tables (idempotent). Safe to call on every startup.

        Also creates the parent directory if missing — Docker / fresh
        deployments where ``/app/data/`` doesn't exist yet would otherwise
        get ``sqlite3.OperationalError: unable to open database file``
        before the schema steps. ``:memory:`` is recognized and skipped.
        """
        if self._db_path != ":memory:":
            from pathlib import Path  # noqa: PLC0415 — stdlib, lazy to keep top imports tight

            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            for stmt in _SCHEMA_SQLITE:
                await conn.execute(stmt)
            await conn.commit()

    async def list_tables(self) -> set[str]:
        """Return the set of table names in the DB (for tests/diagnostics)."""
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            rows = await cursor.fetchall()
        return {row[0] for row in rows}

    async def create_thread(
        self,
        *,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        thread_id = f"thr_{secrets.token_urlsafe(16)}"
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "INSERT INTO hara_threads "
                "(thread_id, title, metadata, created_at, last_active_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (thread_id, title, _json_or_none(metadata), now, now),
            )
            await conn.commit()
        return thread_id

    async def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                "SELECT thread_id, title, metadata, created_at, last_active_at "
                "FROM hara_threads WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    async def record_turn(
        self,
        *,
        thread_id: str,
        question: str,
        answer: str,
        citations: list[dict[str, Any]],
        metadata: dict[str, Any],
        turn_id: str | None = None,
        status: Literal["running", "completed", "failed", "canceled"] = "completed",
    ) -> str:
        """Insert a row into hara_turns. Returns the new turn_id.

        Also bumps the parent thread's last_active_at. If ``turn_id`` is None
        we generate one (``trn_<token>``); the orchestrator passes its own so
        ``TurnResult.turn_id`` and the persisted row stay consistent.

        ``status='running'`` is used by the API route handler to pre-create the
        row before kicking off the async runner; the runner later flips it to
        a terminal status via :meth:`update_turn_status`.
        """
        tid = turn_id if turn_id is not None else f"trn_{secrets.token_urlsafe(12)}"
        now = time.time()
        # ``completed_at`` only carries a value once the turn reaches a
        # terminal status. For ``running`` rows pre-created by the API
        # routes we leave it NULL — :meth:`update_turn_status` /
        # :meth:`update_turn_result` set it (or ``canceled_at``) when the
        # row transitions. Codex review #4 P2: writing ``now`` here
        # produced bogus completion timestamps for in-flight turns.
        completed_at = now if status != "running" else None
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO hara_turns (
                    turn_id, thread_id, status, question, answer,
                    citations, metadata, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid,
                    thread_id,
                    status,
                    question,
                    answer,
                    json.dumps(citations),
                    json.dumps(metadata),
                    now,
                    completed_at,
                ),
            )
            await conn.execute(
                "UPDATE hara_threads SET last_active_at = ? WHERE thread_id = ?",
                (now, thread_id),
            )
            await conn.commit()
        return tid

    async def update_turn_status(
        self,
        *,
        turn_id: str,
        status: Literal["completed", "failed", "canceled"],
    ) -> None:
        """Flip an existing ``hara_turns`` row's status without re-inserting.

        Used by ApiTurnRunner: the row is created up-front (status='running'
        in the route handler); on completion/failure/cancellation we update
        ``status`` plus the matching timestamp column (``canceled_at`` for
        ``canceled``, ``completed_at`` otherwise — the schema only has these
        two terminal-time columns).
        """
        now = time.time()
        column = "canceled_at" if status == "canceled" else "completed_at"
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                f"UPDATE hara_turns SET status = ?, {column} = ? WHERE turn_id = ?",  # noqa: S608
                (status, now, turn_id),
            )
            await conn.commit()

    async def update_turn_result(
        self,
        *,
        turn_id: str,
        answer: str,
        citations: list[dict[str, Any]],
        metadata: dict[str, Any],
        status: Literal["completed", "failed", "canceled"] = "completed",
    ) -> None:
        """Persist the final ``answer``/``citations``/``metadata`` onto an
        existing ``hara_turns`` row and bump ``status`` + the matching
        terminal-time column.

        Used by :meth:`Orchestrator.run_turn` when the caller pre-created the
        row (API path: route handler inserts ``status='running'`` so events
        can FK to it; the orchestrator finalizes the row here without
        creating a duplicate). Also bumps the parent thread's
        ``last_active_at`` to mirror :meth:`record_turn`.
        """
        now = time.time()
        column = "canceled_at" if status == "canceled" else "completed_at"
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                f"""
                UPDATE hara_turns
                SET status = ?, answer = ?, citations = ?, metadata = ?,
                    {column} = ?
                WHERE turn_id = ?
                """,  # noqa: S608
                (
                    status,
                    answer,
                    json.dumps(citations),
                    json.dumps(metadata),
                    now,
                    turn_id,
                ),
            )
            # Mirror record_turn's last_active_at bump, but only if the row
            # exists — defensive for cases where the row was never created.
            if cursor.rowcount > 0:
                await conn.execute(
                    """
                    UPDATE hara_threads
                    SET last_active_at = ?
                    WHERE thread_id = (
                        SELECT thread_id FROM hara_turns WHERE turn_id = ?
                    )
                    """,
                    (now, turn_id),
                )
            await conn.commit()

    async def list_turns(
        self,
        *,
        thread_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` most recent turns for ``thread_id``, newest first.

        Returns ``[]`` for unknown ``thread_id`` (no raise — caller can check
        via ``get_thread`` if it needs to distinguish unknown from empty).
        """
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT turn_id, thread_id, status, question, answer,
                       citations, metadata, started_at, completed_at,
                       canceled_at
                FROM hara_turns
                WHERE thread_id = ?
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (thread_id, limit),
            )
            rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["citations"] = json.loads(d["citations"]) if d["citations"] else []
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else {}
            out.append(d)
        return out

    async def record_ingested_file(self, rec: IngestedFileRecord) -> None:
        """Insert or replace a row in ``hara_ingested_files``.

        Uses ``INSERT ... ON CONFLICT(content_hash, target) DO UPDATE`` so the
        primary key acts as an upsert key. Re-ingesting the same bytes into
        the same target replaces the existing record (e.g., chunk count
        changes after a chunker tweak).
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                """
                INSERT INTO hara_ingested_files (
                    content_hash, target, relative_path, table_or_file_id,
                    ingested_at, chunks_count, rows_count, tokens_used
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_hash, target) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    table_or_file_id = excluded.table_or_file_id,
                    ingested_at = excluded.ingested_at,
                    chunks_count = excluded.chunks_count,
                    rows_count = excluded.rows_count,
                    tokens_used = excluded.tokens_used
                """,
                (
                    rec.content_hash,
                    rec.target,
                    rec.relative_path,
                    rec.table_or_file_id,
                    rec.ingested_at.isoformat(),
                    rec.chunks_count,
                    rec.rows_count,
                    rec.tokens_used,
                ),
            )
            await conn.commit()

    async def find_ingested_file(
        self, *, content_hash: str, target: Literal["sql", "vector"]
    ) -> IngestedFileRecord | None:
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT content_hash, target, relative_path, table_or_file_id,
                       ingested_at, chunks_count, rows_count, tokens_used
                FROM hara_ingested_files
                WHERE content_hash = ? AND target = ?
                """,
                (content_hash, target),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return IngestedFileRecord(
            content_hash=row["content_hash"],
            target=row["target"],
            relative_path=row["relative_path"],
            table_or_file_id=row["table_or_file_id"],
            ingested_at=datetime.fromisoformat(row["ingested_at"]),
            chunks_count=row["chunks_count"],
            rows_count=row["rows_count"],
            tokens_used=row["tokens_used"],
        )

    async def list_ingested_files(
        self, *, target: Literal["sql", "vector"] | None = None
    ) -> list[IngestedFileRecord]:
        sql = (
            "SELECT content_hash, target, relative_path, table_or_file_id, "
            "ingested_at, chunks_count, rows_count, tokens_used "
            "FROM hara_ingested_files"
        )
        params: tuple[str, ...] = ()
        if target is not None:
            sql += " WHERE target = ?"
            params = (target,)
        sql += " ORDER BY ingested_at DESC"

        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        return [
            IngestedFileRecord(
                content_hash=r["content_hash"],
                target=r["target"],
                relative_path=r["relative_path"],
                table_or_file_id=r["table_or_file_id"],
                ingested_at=datetime.fromisoformat(r["ingested_at"]),
                chunks_count=r["chunks_count"],
                rows_count=r["rows_count"],
                tokens_used=r["tokens_used"],
            )
            for r in rows
        ]

    async def delete_ingested_file(
        self, *, content_hash: str, target: Literal["sql", "vector"]
    ) -> None:
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "DELETE FROM hara_ingested_files WHERE content_hash = ? AND target = ?",
                (content_hash, target),
            )
            await conn.commit()

    async def delete_ingested_files_by_target_id(
        self, *, target: Literal["sql", "vector"], table_or_file_id: str
    ) -> None:
        """Remove every row matching ``(target, table_or_file_id)``.

        Used by the ``replace`` mode of both pipelines: when a file's bytes
        change, the old ``content_hash`` row would otherwise stay around and
        skew the snapshot used by the semantic-map regen check.
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute(
                "DELETE FROM hara_ingested_files WHERE target = ? AND table_or_file_id = ?",
                (target, table_or_file_id),
            )
            await conn.commit()

    async def list_threads(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to ``limit`` threads ordered by ``last_active_at`` desc."""
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(
                """
                SELECT thread_id, title, metadata, created_at, last_active_at
                FROM hara_threads
                ORDER BY last_active_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["metadata"] = json.loads(d["metadata"]) if d["metadata"] else None
            out.append(d)
        return out

    async def delete_thread(self, thread_id: str) -> bool:
        """Remove a thread + cascade. Returns True if it existed.

        ``ON DELETE CASCADE`` declared in the schema (Plano 1) takes care of
        ``hara_turns`` and ``hara_events`` rows.
        """
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            cursor = await conn.execute(
                "DELETE FROM hara_threads WHERE thread_id = ?",
                (thread_id,),
            )
            await conn.commit()
            return cursor.rowcount > 0

    async def record_event(
        self,
        *,
        turn_id: str,
        event_type: Literal["state", "token", "final", "done"],
        data: dict[str, Any],
    ) -> int:
        """Append one row to ``hara_events`` and return the auto-incremented ``seq``.

        Caller (ApiTurnRunner / SSE pump) is responsible for publishing to
        the in-process queue *after* this returns — so replay (DB-only) is
        always a strict prefix of the live tail, no race.
        """
        now = time.time()
        async with aiosqlite.connect(self._db_path) as conn:
            cursor = await conn.execute(
                "INSERT INTO hara_events (turn_id, event_type, data, ts) VALUES (?, ?, ?, ?)",
                (turn_id, event_type, json.dumps(data), now),
            )
            await conn.commit()
            return int(cursor.lastrowid or 0)

    async def list_events(
        self,
        *,
        turn_id: str,
        after_seq: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return events for ``turn_id`` ordered by ``seq`` ascending.

        ``after_seq`` filters strictly greater than that value (for
        reconnect-resume semantics; v0.1 SSE only uses full replay).
        """
        sql = "SELECT seq, turn_id, event_type, data, ts FROM hara_events WHERE turn_id = ?"
        params: list[Any] = [turn_id]
        if after_seq is not None:
            sql += " AND seq > ?"
            params.append(after_seq)
        sql += " ORDER BY seq ASC"
        async with aiosqlite.connect(self._db_path) as conn:
            conn.row_factory = aiosqlite.Row
            cursor = await conn.execute(sql, tuple(params))
            rows = await cursor.fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"]) if d["data"] else {}
            out.append(d)
        return out

    async def cleanup_expired(self, *, older_than_seconds: float) -> None:
        """Remove turns + cascading events older than ``older_than_seconds``
        relative to ``time.time()``. Used at ``hara serve`` startup
        (spec §6 ttl_days x 86400)."""
        cutoff = time.time() - older_than_seconds
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute(
                "DELETE FROM hara_turns WHERE started_at < ?",
                (cutoff,),
            )
            await conn.commit()


def _json_or_none(data: dict[str, Any] | None) -> str | None:
    return json.dumps(data) if data is not None else None


def derive_session_dsn(settings: Settings) -> str:
    """Convert ``settings.connectors.session_store`` to an aiosqlite-compatible DSN.

    The default config (``SessionStoreInheritConfig``) means *inherit from
    [connectors.sql]* - practical only when the SQL connector itself is
    SQLite/memory in v0.1. Postgres session-store DSNs raise
    ``NotImplementedError`` for now (deferred to v0.2 once SessionStore
    grows asyncpg support).

    Note: SQLite's in-memory string is ``:memory:``. Since ``SessionStore``
    parses DSNs by stripping the ``sqlite:///`` prefix (three slashes), the
    correct DSN form here is ``sqlite:///:memory:`` - after the strip we
    end up with the literal ``:memory:`` that aiosqlite expects.
    """
    from hara.config.schemas import (  # noqa: PLC0415 - runtime import to avoid cycle
        MemorySQLConfig,
        SessionStoreMemoryConfig,
        SessionStorePostgresConfig,
        SessionStoreSQLiteConfig,
        SQLiteSQLConfig,
    )

    cs = settings.connectors.session_store
    if isinstance(cs, SessionStoreSQLiteConfig):
        return f"sqlite:///{cs.path}"
    if isinstance(cs, SessionStoreMemoryConfig):
        return "sqlite:///:memory:"
    if isinstance(cs, SessionStorePostgresConfig):
        raise NotImplementedError(
            "Postgres session-store DSN not supported in v0.1; use sqlite or inherit"
        )
    # Remaining branch: SessionStoreInheritConfig (Pyright narrows automatically).
    sql = settings.connectors.sql
    if isinstance(sql, SQLiteSQLConfig):
        return f"sqlite:///{sql.path}"
    if isinstance(sql, MemorySQLConfig):
        return "sqlite:///:memory:"
    raise NotImplementedError(
        "session_store inherit from non-SQLite SQL connector not supported in v0.1"
    )
