"""Tests for the per-turn asyncio event queue."""

from __future__ import annotations

import asyncio

from hara.api.events import EventBus


async def test_publish_then_subscribe_delivers() -> None:
    bus = EventBus()
    queue = bus.subscribe("trn_x")
    await bus.publish("trn_x", {"event": "state", "data": {"node": "planner"}})
    msg = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert msg == {"event": "state", "data": {"node": "planner"}}


async def test_publish_with_no_subscriber_does_not_raise() -> None:
    """If no SSE consumer connected, publish is a no-op (events still get
    persisted by the caller)."""
    bus = EventBus()
    await bus.publish("trn_x", {"event": "token", "data": {"text": "hi"}})


async def test_multiple_subscribers_each_receive_copy() -> None:
    bus = EventBus()
    q1 = bus.subscribe("trn_x")
    q2 = bus.subscribe("trn_x")
    await bus.publish("trn_x", {"event": "done", "data": {}})
    m1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    m2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert m1 == m2 == {"event": "done", "data": {}}


async def test_unsubscribe_removes_queue() -> None:
    bus = EventBus()
    q = bus.subscribe("trn_x")
    bus.unsubscribe("trn_x", q)
    # No subscribers — publish is silently dropped.
    await bus.publish("trn_x", {"event": "done", "data": {}})


async def test_close_turn_drains_subscribers() -> None:
    """When the turn finishes (or is cancelled), close_turn signals all
    subscribers via a sentinel ``None`` so SSE pumps can exit."""
    bus = EventBus()
    q = bus.subscribe("trn_x")
    await bus.close_turn("trn_x")
    msg = await asyncio.wait_for(q.get(), timeout=1.0)
    assert msg is None
