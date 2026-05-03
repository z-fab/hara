"""Convenience endpoints — /invoke (sync) and /stream (SSE)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from hara.api.deps import require_auth
from hara.api.errors import (
    ConfigError,
    LLMProviderError,
    MessageTooLongError,
    ThreadNotFoundError,
)
from hara.api.runner import ApiTurnRunner, new_turn_id
from hara.api.schemas import InvokeRequest, InvokeResponse
from hara.api.sse import event_stream

router = APIRouter(tags=["convenience"], dependencies=[Depends(require_auth)])

_ORCH_UNAVAILABLE_MSG = (
    "Orchestrator unavailable; check provider credentials and run `hara doctor`."
)


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(req: InvokeRequest, request: Request) -> InvokeResponse:
    if request.app.state.orchestrator is None:
        raise ConfigError(_ORCH_UNAVAILABLE_MSG)

    settings = request.app.state.settings
    if len(req.message) > settings.api.max_message_length:
        raise MessageTooLongError(
            f"message exceeds max_message_length ({settings.api.max_message_length})",
            details={
                "max_message_length": settings.api.max_message_length,
                "received": len(req.message),
            },
        )

    store = request.app.state.store
    bus = request.app.state.bus
    registry = request.app.state.registry
    orchestrator = request.app.state.orchestrator

    if req.thread_id is not None and await store.get_thread(req.thread_id) is None:
        raise ThreadNotFoundError(f"Thread {req.thread_id} not found")

    thread_id = req.thread_id or await store.create_thread()
    turn_id = new_turn_id()
    await store.record_turn(
        thread_id=thread_id,
        turn_id=turn_id,
        question=req.message,
        answer="",
        citations=[],
        metadata={},
        status="running",
    )
    runner = ApiTurnRunner(orchestrator=orchestrator, store=store, bus=bus)
    task = asyncio.create_task(
        runner.run(thread_id=thread_id, question=req.message, turn_id=turn_id)
    )
    registry.register(turn_id, task)
    try:
        await task
    finally:
        registry.unregister(turn_id)

    rows = await store.list_turns(thread_id=thread_id, limit=10)
    row = next(r for r in rows if r["turn_id"] == turn_id)
    # Codex review #4 P1: the runner swallows orchestrator exceptions and
    # marks the row 'failed' (so SSE consumers see done + the persisted
    # row reflects the failure). The sync /invoke must surface that as
    # 502 — spec §6 lists LLM_PROVIDER_ERROR / CONNECTOR_ERROR /
    # SQL_VALIDATION_ERROR as the failure modes. Without this check
    # /invoke would return 200 with an empty answer.
    if row["status"] == "failed":
        raise LLMProviderError("turn execution failed")
    # ``canceled`` stays a 200 per spec §6 'TURN_CANCELED 200' — caller
    # gets whatever partial state the row holds; the canonical
    # cancellation flow is DELETE + GET turn detail.
    return InvokeResponse(
        turn_id=turn_id,
        thread_id=thread_id,
        answer=row.get("answer") or "",
        citations=row.get("citations") or [],
        metadata=row.get("metadata") or {},
    )


@router.post("/stream")
async def stream(req: InvokeRequest, request: Request) -> StreamingResponse:
    """Same as /invoke but streams SSE events as they happen."""
    if request.app.state.orchestrator is None:
        raise ConfigError(_ORCH_UNAVAILABLE_MSG)

    settings = request.app.state.settings
    if len(req.message) > settings.api.max_message_length:
        raise MessageTooLongError(
            f"message exceeds max_message_length ({settings.api.max_message_length})",
            details={
                "max_message_length": settings.api.max_message_length,
                "received": len(req.message),
            },
        )

    store = request.app.state.store
    bus = request.app.state.bus
    registry = request.app.state.registry
    orchestrator = request.app.state.orchestrator

    if req.thread_id is not None and await store.get_thread(req.thread_id) is None:
        raise ThreadNotFoundError(f"Thread {req.thread_id} not found")

    thread_id = req.thread_id or await store.create_thread()
    turn_id = new_turn_id()
    await store.record_turn(
        thread_id=thread_id,
        turn_id=turn_id,
        question=req.message,
        answer="",
        citations=[],
        metadata={},
        status="running",
    )

    # Subscribe BEFORE starting the runner so no events are lost.
    queue = bus.subscribe(turn_id)

    runner = ApiTurnRunner(orchestrator=orchestrator, store=store, bus=bus)
    task = asyncio.create_task(
        runner.run(thread_id=thread_id, question=req.message, turn_id=turn_id)
    )
    registry.register(turn_id, task)
    task.add_done_callback(lambda _t: registry.unregister(turn_id))

    async def _gen() -> AsyncIterator[bytes]:
        try:
            # Fresh stream — no replay needed; dedupe_seq=0 means accept all.
            async for chunk in event_stream(replay=[], queue=queue, dedupe_seq=0):
                yield chunk
        finally:
            bus.unsubscribe(turn_id, queue)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
