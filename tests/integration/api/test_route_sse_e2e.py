"""SSE: stream events; replay after done."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_sse_streams_events_to_done(client: tuple[Any, Any]) -> None:
    ac, _app = client

    create = await ac.post("/threads", json={})
    tid = create.json()["thread_id"]
    msg = await ac.post(f"/threads/{tid}/messages", json={"message": "x"})
    turn_id = msg.json()["turn_id"]

    # Wait briefly for the runner to land events
    await asyncio.sleep(0.1)

    chunks: list[str] = []
    async with ac.stream("GET", f"/threads/{tid}/turns/{turn_id}/events") as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        async for raw in r.aiter_text():
            chunks.append(raw)
            joined = "".join(chunks)
            if "event: done" in joined:
                break
    body = "".join(chunks)
    assert "event: state" in body
    assert "event: token" in body
    assert "event: final" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_sse_replay_after_done(client: tuple[Any, Any]) -> None:
    """After turn finishes, opening /events again replays all events without hanging."""
    ac, _app = client

    create = await ac.post("/threads", json={})
    tid = create.json()["thread_id"]
    msg = await ac.post(f"/threads/{tid}/messages", json={"message": "x"})
    turn_id = msg.json()["turn_id"]

    # Wait for completion
    r = None
    for _ in range(30):
        r = await ac.get(f"/threads/{tid}/turns/{turn_id}")
        if r.status_code == 200:
            break
        await asyncio.sleep(0.05)
    assert r is not None
    assert r.status_code == 200

    # Now open SSE — should replay and close, not hang.
    chunks: list[str] = []
    async with ac.stream("GET", f"/threads/{tid}/turns/{turn_id}/events") as resp:
        async for raw in resp.aiter_text():
            chunks.append(raw)
            if "event: done" in "".join(chunks):
                break
    body = "".join(chunks)
    assert "event: done" in body
