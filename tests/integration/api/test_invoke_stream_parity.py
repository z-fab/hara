"""Spec §17 critério 10: /invoke and /stream produce identical final."""

from __future__ import annotations

import json
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_invoke_and_stream_same_final(client: tuple[Any, Any]) -> None:
    ac, _app = client

    invoke = await ac.post("/invoke", json={"message": "same question"})
    assert invoke.status_code == 200
    invoke_body = invoke.json()

    # Now /stream the same question and reconstruct the final
    chunks: list[str] = []
    async with ac.stream("POST", "/stream", json={"message": "same question"}) as r:
        assert r.status_code == 200
        async for raw in r.aiter_text():
            chunks.append(raw)
            if "event: done" in "".join(chunks):
                break
    body = "".join(chunks)

    # Extract the final event's data. Wire format from sse.encode_event:
    # b"event: <type>\ndata: <json>\n\n".
    marker = "event: final\ndata: "
    idx = body.find(marker)
    assert idx >= 0, f"no final event in stream: {body[:200]}"
    data_start = idx + len(marker)
    data_end = body.find("\n\n", data_start)
    final_json = body[data_start:data_end]
    final_payload = json.loads(final_json)

    # Compare answers + citations
    assert final_payload["answer"] == invoke_body["answer"]
    assert final_payload["citations"] == invoke_body["citations"]
