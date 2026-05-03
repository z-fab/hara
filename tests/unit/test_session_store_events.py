"""Tests for SessionStore.record_event + list_events + cleanup_expired."""

from __future__ import annotations

import pytest

from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path):
    s = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await s.initialize()
    return s


async def test_record_and_list_events(store: SessionStore) -> None:
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="q",
        answer="",
        citations=[],
        metadata={},
    )
    seq1 = await store.record_event(turn_id=tid, event_type="state", data={"node": "planner"})
    seq2 = await store.record_event(turn_id=tid, event_type="token", data={"text": "Hello "})
    seq3 = await store.record_event(turn_id=tid, event_type="token", data={"text": "world"})

    events = await store.list_events(turn_id=tid)
    assert [e["event_type"] for e in events] == ["state", "token", "token"]
    assert [e["seq"] for e in events] == sorted([seq1, seq2, seq3])
    assert events[0]["data"] == {"node": "planner"}


async def test_list_events_supports_seq_offset(store: SessionStore) -> None:
    """For SSE replay-then-tail, caller may want events strictly after seq=N
    when reconnecting after a disconnect. v0.1 only needs full replay,
    but the API is shaped for future reconnect support."""
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="q",
        answer="",
        citations=[],
        metadata={},
    )
    s1 = await store.record_event(turn_id=tid, event_type="state", data={"x": 1})
    _ = await store.record_event(turn_id=tid, event_type="state", data={"x": 2})

    after = await store.list_events(turn_id=tid, after_seq=s1)
    assert len(after) == 1
    assert after[0]["data"] == {"x": 2}


async def test_list_events_unknown_turn_returns_empty(store: SessionStore) -> None:
    assert await store.list_events(turn_id="trn_nope") == []


async def test_cleanup_expired_removes_old_turns_and_events(store: SessionStore) -> None:
    """TTL cleanup: turns older than ttl with no recent activity get pruned
    (cascades to events via ON DELETE CASCADE)."""
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="q",
        answer="",
        citations=[],
        metadata={},
    )
    await store.record_event(turn_id=tid, event_type="state", data={})

    # Sanity baseline
    assert len(await store.list_events(turn_id=tid)) == 1

    # Clean up anything older than 0 seconds → everything we just inserted.
    await store.cleanup_expired(older_than_seconds=0)

    assert await store.list_events(turn_id=tid) == []
    assert await store.list_turns(thread_id=thr, limit=10) == []
