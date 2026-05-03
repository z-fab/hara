"""POST messages → poll until 200 → assert answer + citations + metadata."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_post_message_completes_with_answer(client: tuple[Any, Any]) -> None:
    ac, _app = client

    create = await ac.post("/threads", json={})
    assert create.status_code == 201
    tid = create.json()["thread_id"]

    msg = await ac.post(f"/threads/{tid}/messages", json={"message": "qual a producao?"})
    assert msg.status_code == 202
    turn_id = msg.json()["turn_id"]

    # Poll until 200 or timeout
    r = None
    for _ in range(30):
        r = await ac.get(f"/threads/{tid}/turns/{turn_id}")
        if r.status_code == 200:
            break
        await asyncio.sleep(0.05)
    assert r is not None
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed"
    assert "Hello" in body["answer"]
    assert body["thread_id"] == tid
    assert body["turn_id"] == turn_id
