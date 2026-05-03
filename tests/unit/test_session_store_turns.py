"""Tests for SessionStore.record_turn + list_turns (Plano 3 multi-turn glue)."""

from __future__ import annotations

import pytest

from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path):
    s = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await s.initialize()
    return s


async def test_record_and_list_turn(store: SessionStore) -> None:
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="oi",
        answer="olá",
        citations=[{"evidence_id": 1, "kind": "sql"}],
        metadata={"latency_per_node": {"planner": 0.1}},
    )
    assert tid.startswith("trn_")

    turns = await store.list_turns(thread_id=thr, limit=10)
    assert len(turns) == 1
    assert turns[0]["question"] == "oi"
    assert turns[0]["answer"] == "olá"
    assert turns[0]["citations"] == [{"evidence_id": 1, "kind": "sql"}]
    assert turns[0]["metadata"] == {"latency_per_node": {"planner": 0.1}}


async def test_list_turns_orders_by_started_at_desc(store: SessionStore) -> None:
    """Newest first."""
    thr = await store.create_thread()
    await store.record_turn(thread_id=thr, question="a", answer="x", citations=[], metadata={})
    await store.record_turn(thread_id=thr, question="b", answer="y", citations=[], metadata={})
    await store.record_turn(thread_id=thr, question="c", answer="z", citations=[], metadata={})

    turns = await store.list_turns(thread_id=thr, limit=2)
    assert len(turns) == 2
    questions_order = [t["question"] for t in turns]
    assert questions_order == ["c", "b"]


async def test_list_turns_empty_thread(store: SessionStore) -> None:
    thr = await store.create_thread()
    assert await store.list_turns(thread_id=thr, limit=10) == []


async def test_list_turns_unknown_thread_returns_empty(store: SessionStore) -> None:
    """No raise; just empty list."""
    assert await store.list_turns(thread_id="nope", limit=10) == []


async def test_list_turns_includes_canceled_at(store: SessionStore) -> None:
    """Codex review #3 P3: ``list_turns`` SELECT was missing ``canceled_at``,
    so ``TurnOut.canceled_at`` was always None even after a DELETE flipped
    the row to ``status='canceled'`` with a real timestamp."""
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="q",
        answer="",
        citations=[],
        metadata={},
        status="running",
    )
    await store.update_turn_status(turn_id=tid, status="canceled")

    rows = await store.list_turns(thread_id=thr, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "canceled"
    assert row["canceled_at"] is not None
    assert isinstance(row["canceled_at"], float)


async def test_record_turn_running_leaves_completed_at_null(
    store: SessionStore,
) -> None:
    """Codex review #4 P2: pre-creating a turn row with ``status='running'``
    must NOT write ``completed_at=now()`` — that column is reserved for
    terminal transitions and was producing bogus completion timestamps
    for in-flight turns."""
    thr = await store.create_thread()
    tid = await store.record_turn(
        thread_id=thr,
        question="q",
        answer="",
        citations=[],
        metadata={},
        status="running",
    )
    rows = await store.list_turns(thread_id=thr, limit=10)
    assert len(rows) == 1
    row = rows[0]
    assert row["turn_id"] == tid
    assert row["status"] == "running"
    assert row["completed_at"] is None
    # canceled_at is NULL too — no terminal transition has happened.
    assert row["canceled_at"] is None


async def test_record_turn_completed_sets_completed_at(
    store: SessionStore,
) -> None:
    """Sanity guard for the change above: terminal statuses still
    populate ``completed_at`` immediately."""
    thr = await store.create_thread()
    await store.record_turn(
        thread_id=thr,
        question="q",
        answer="a",
        citations=[],
        metadata={},
        status="completed",
    )
    rows = await store.list_turns(thread_id=thr, limit=10)
    assert rows[0]["completed_at"] is not None
    assert isinstance(rows[0]["completed_at"], float)
