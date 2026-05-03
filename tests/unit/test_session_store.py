"""Tests for Session Store schema creation (idempotent)."""

from __future__ import annotations

import pytest

from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path) -> SessionStore:
    db_path = tmp_path / "test.db"
    s = SessionStore(dsn=f"sqlite:///{db_path}")
    await s.initialize()
    return s


async def test_initialize_creates_all_tables(store: SessionStore) -> None:
    expected_tables = {
        "hara_threads",
        "hara_turns",
        "hara_events",
        "hara_ingested_files",
    }
    actual = await store.list_tables()
    assert expected_tables <= actual


async def test_initialize_is_idempotent(store: SessionStore) -> None:
    # Running again must not raise nor duplicate
    await store.initialize()
    await store.initialize()
    actual = await store.list_tables()
    assert "hara_threads" in actual


async def test_create_and_get_thread(store: SessionStore) -> None:
    thread_id = await store.create_thread(title="Test thread")
    assert thread_id.startswith("thr_")

    info = await store.get_thread(thread_id)
    assert info is not None
    assert info["title"] == "Test thread"


async def test_get_unknown_thread_returns_none(store: SessionStore) -> None:
    assert await store.get_thread("thr_nonexistent") is None
