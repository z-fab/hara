"""Tests for ApiTurnRunner — the API's bridge to Orchestrator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from hara.agent.orchestrator import Orchestrator, TurnResult
from hara.api.events import EventBus
from hara.api.runner import ApiTurnRunner, new_turn_id
from hara.services.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await s.initialize()
    return s


class _FakeOrch:
    """Minimal Orchestrator stand-in: emits scripted events; returns a fixed TurnResult."""

    def __init__(self, *, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def run_turn(
        self,
        _question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Any = None,
        on_event: Any = None,
    ) -> Any:
        for ev in self._events:
            if on_event is not None:
                await on_event(ev)
        return TurnResult(
            thread_id=thread_id or "thr_x",
            turn_id=turn_id or "trn_x",
            answer="resp",
            citations=[],
            metadata={
                "routing": {},
                "verifier": None,
                "tokens": {},
                "latency_per_node": {},
                "unsupported_markers": 0,
            },
        )


async def test_runner_persists_and_publishes_events(store: SessionStore) -> None:
    bus = EventBus()
    fake_orch = _FakeOrch(
        events=[
            {"event": "state", "data": {"node": "planner", "status": "started"}},
        ]
    )
    runner = ApiTurnRunner(
        orchestrator=cast(Orchestrator, fake_orch),
        store=store,
        bus=bus,
    )

    thr = await store.create_thread()
    turn_id = new_turn_id()
    await store.record_turn(
        thread_id=thr,
        turn_id=turn_id,
        question="q",
        answer="",
        citations=[],
        metadata={},
        status="completed",
    )

    queue = bus.subscribe(turn_id)
    await runner.run(thread_id=thr, question="q", turn_id=turn_id)

    # Persisted in DB
    events_db = await store.list_events(turn_id=turn_id)
    types_db = [e["event_type"] for e in events_db]
    assert "state" in types_db
    assert "final" in types_db
    assert "done" in types_db

    # Live queue saw the same events (plus the close sentinel).
    received: list[Any] = []
    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=0.5)
        except TimeoutError:
            break
        if msg is None:
            break
        received.append(msg)
    assert any(e["event"] == "final" for e in received)
    # Codex review #2 P2: published events must carry the DB ``seq`` so
    # the SSE generator can dedupe queue entries against the replay's
    # max seq when a subscriber races subscribe→replay.
    seqs = [e["seq"] for e in received]
    assert all(isinstance(s, int) and s > 0 for s in seqs)
    # Strictly ascending (matches insertion order in hara_events).
    assert seqs == sorted(seqs)
