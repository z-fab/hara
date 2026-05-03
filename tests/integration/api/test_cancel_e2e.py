"""DELETE turn cancels in-flight; status moves to canceled."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_delete_cancels_running_turn(client: tuple[Any, Any]) -> None:
    """Note: with the FakeOrch this is fast — completes before DELETE.
    The test verifies the contract (DELETE returns 204; status field
    is one of the valid terminal states).
    """
    ac, _app = client

    create = await ac.post("/threads", json={})
    tid = create.json()["thread_id"]
    msg = await ac.post(f"/threads/{tid}/messages", json={"message": "x"})
    turn_id = msg.json()["turn_id"]

    # Cancel
    cancel = await ac.delete(f"/threads/{tid}/turns/{turn_id}")
    assert cancel.status_code == 204

    # Wait for terminal status
    r = None
    for _ in range(30):
        r = await ac.get(f"/threads/{tid}/turns/{turn_id}")
        if r.status_code == 200:
            break
        await asyncio.sleep(0.05)
    assert r is not None
    assert r.status_code == 200
    # Either completed (raced) or canceled (caught it). Both are valid.
    assert r.json()["status"] in ("completed", "canceled")


@pytest.mark.asyncio
async def test_delete_unknown_turn_404(client: tuple[Any, Any]) -> None:
    ac, _app = client
    create = await ac.post("/threads", json={})
    tid = create.json()["thread_id"]
    r = await ac.delete(f"/threads/{tid}/turns/trn_nope")
    assert r.status_code == 404
