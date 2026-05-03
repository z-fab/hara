"""EventBus — in-process pub/sub for per-turn event streaming.

Each turn's events flow through one or more ``asyncio.Queue`` instances
(one per active SSE subscriber). The agent publishes; SSE pumps consume.

Replay-then-tail invariant (spec §6): the SSE handler in
``routes/turns.py`` reads all persisted events from
``SessionStore.list_events`` first, then subscribes here. Because
``record_event`` is awaited *before* the in-process publish, replay is
guaranteed a prefix of the tail.

A sentinel ``None`` value is used to terminate subscriber loops when the
turn ends; consumers should stop iterating on receipt.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any


class EventBus:
    """Per-turn pub/sub. Single asyncio loop assumed."""

    def __init__(self) -> None:
        self._channels: dict[str, list[asyncio.Queue[Any]]] = {}

    def subscribe(self, turn_id: str) -> asyncio.Queue[Any]:
        q: asyncio.Queue[Any] = asyncio.Queue()
        self._channels.setdefault(turn_id, []).append(q)
        return q

    def unsubscribe(self, turn_id: str, q: asyncio.Queue[Any]) -> None:
        subs = self._channels.get(turn_id)
        if subs is None:
            return
        with contextlib.suppress(ValueError):
            subs.remove(q)
        if not subs:
            del self._channels[turn_id]

    async def publish(self, turn_id: str, event: dict[str, Any]) -> None:
        for q in list(self._channels.get(turn_id, [])):
            await q.put(event)

    async def close_turn(self, turn_id: str) -> None:
        """Push the ``None`` sentinel so subscribers exit their loops."""
        for q in list(self._channels.get(turn_id, [])):
            await q.put(None)
