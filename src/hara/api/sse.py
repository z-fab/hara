"""SSE response helper.

Spec §6: ``async def`` + ``yield`` returning ``AsyncIterator[bytes]``
wrapped in ``StreamingResponse(media_type="text/event-stream")``. No
``sse-starlette`` dep — FastAPI handles it natively.

Each event is encoded as:

    event: <type>
    data: <json>
    \\n

A keepalive comment (": ping") is sent every 15s to defeat proxies'
idle-timeout.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

_KEEPALIVE_SECONDS = 15.0


def encode_event(event_type: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"), default=str)
    return f"event: {event_type}\ndata: {payload}\n\n".encode()


async def event_stream(
    *,
    replay: list[dict[str, Any]],
    queue: asyncio.Queue[Any],
    dedupe_seq: int = 0,
    close_after_replay: bool = False,
) -> AsyncIterator[bytes]:
    """Stream events: replay first, then tail from queue.

    ``None`` on queue means end. ``dedupe_seq`` is the highest ``seq``
    observed in ``replay`` — queue messages whose ``seq`` is ``<=`` that
    value were already delivered as part of replay and are dropped here.
    The route subscribes to the bus *before* snapshotting from DB to make
    sure no in-flight event is lost; this dedupe collapses the resulting
    overlap to a single delivery per event.

    ``close_after_replay`` is set by the route handler when the turn is
    already in a terminal status (completed/failed/canceled) at subscribe
    time. In that case ``close_turn`` has long since delivered its
    ``None`` sentinel to the (now-gone) prior subscribers; the queue we
    just subscribed to will never receive one. Yield replay and return —
    no live tail.
    """
    for ev in replay:
        yield encode_event(ev["event_type"], ev["data"])

    if close_after_replay:
        return

    while True:
        try:
            msg = await asyncio.wait_for(queue.get(), timeout=_KEEPALIVE_SECONDS)
        except TimeoutError:
            yield b": ping\n\n"
            continue
        if msg is None:
            return
        # ApiTurnRunner attaches ``seq`` when publishing. Older publishers
        # (or tests) may omit it — treat missing seq as a fresh event.
        msg_seq = msg.get("seq")
        if isinstance(msg_seq, int) and msg_seq <= dedupe_seq:
            continue
        yield encode_event(msg["event"], msg["data"])
