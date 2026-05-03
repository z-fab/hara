"""Tests for SessionStore.list_threads + delete_thread."""

from __future__ import annotations

import pytest

from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path):
    s = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await s.initialize()
    return s


async def test_list_threads_empty(store: SessionStore) -> None:
    assert await store.list_threads() == []


async def test_list_threads_orders_by_last_active_desc(store: SessionStore) -> None:
    """Spec §6: GET /threads is a list — newest first by activity."""
    t1 = await store.create_thread(title="oldest")
    _ = await store.create_thread(title="middle")
    _ = await store.create_thread(title="newest")
    # Bump last_active on t1 by recording a turn there
    await store.record_turn(
        thread_id=t1,
        question="x",
        answer="y",
        citations=[],
        metadata={},
    )
    rows = await store.list_threads()
    assert len(rows) == 3
    # t1 was bumped → first
    assert rows[0]["thread_id"] == t1


async def test_list_threads_supports_limit(store: SessionStore) -> None:
    for _ in range(5):
        await store.create_thread()
    rows = await store.list_threads(limit=3)
    assert len(rows) == 3


async def test_delete_thread_removes_thread(store: SessionStore) -> None:
    tid = await store.create_thread()
    assert await store.get_thread(tid) is not None
    deleted = await store.delete_thread(tid)
    assert deleted is True
    assert await store.get_thread(tid) is None


async def test_delete_thread_returns_false_on_unknown(store: SessionStore) -> None:
    assert await store.delete_thread("thr_nonexistent") is False


async def test_delete_thread_cascades_to_turns_and_events(store: SessionStore) -> None:
    """ON DELETE CASCADE on hara_turns + hara_events should leave no orphans."""
    tid = await store.create_thread()
    turn_id = await store.record_turn(
        thread_id=tid,
        question="q",
        answer="a",
        citations=[],
        metadata={},
    )
    await store.record_event(
        turn_id=turn_id,
        event_type="state",
        data={"node": "planner"},
    )

    await store.delete_thread(tid)
    assert await store.list_turns(thread_id=tid, limit=10) == []
    assert await store.list_events(turn_id=turn_id) == []
