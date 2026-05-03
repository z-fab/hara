"""TTL cleanup: stale turn rows are removed at startup."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import httpx
import pytest

from hara.api.app import create_app
from hara.config.schemas import SessionStoreSQLiteConfig
from hara.services.session_store import SessionStore
from tests.integration.api.conftest import _settings  # pyright: ignore[reportPrivateUsage]


@pytest.fixture
async def fixture_with_stale_row(
    tmp_path: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, str]]:
    """Pre-seed a stale turn into hara_turns BEFORE the app boots; on startup
    the lifespan TTL cleanup should drop it (with ttl_days=0 the cleanup is
    skipped, so use ttl_days=1 and an old started_at timestamp far past the
    cutoff).
    """
    settings = _settings(tmp_path)
    # ttl_days=1; the seeded started_at=0 (epoch) is well past the cutoff.
    settings.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
        ttl_days=1,
    )

    # Seed: create thread + insert an old turn directly in the DB.
    seeder = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await seeder.initialize()
    thr = await seeder.create_thread()
    async with aiosqlite.connect(f"{tmp_path}/sess.db") as conn:
        await conn.execute(
            "INSERT INTO hara_turns (turn_id, thread_id, status, question, answer, "
            "started_at, completed_at) "
            "VALUES (?, ?, 'completed', 'q', 'a', 0.0, 0.0)",
            ("trn_stale", thr),
        )
        await conn.commit()

    # Now boot the app — startup should call cleanup_expired and drop the stale row.
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as ac,
        app.router.lifespan_context(app),
    ):
        yield ac, app, thr


@pytest.mark.asyncio
async def test_ttl_cleanup_removes_stale_turn(
    fixture_with_stale_row: tuple[Any, Any, str],
) -> None:
    _ac, app, thr = fixture_with_stale_row

    store: SessionStore = app.state.store
    rows = await store.list_turns(thread_id=thr, limit=10)
    # The stale row was inserted with started_at=0; cleanup_expired with
    # ttl_days=1 should have dropped it (cutoff = now - 86400).
    assert all(r["turn_id"] != "trn_stale" for r in rows)
