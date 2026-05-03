"""Turn endpoints — POST messages, GET turns, GET events SSE, DELETE cancel.

This file accumulates the endpoints in Tasks 14-16. Task 14 lands POST messages.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

from hara.api.deps import require_auth
from hara.api.errors import (
    ConfigError,
    MessageTooLongError,
    ThreadNotFoundError,
    TurnNotFoundError,
    TurnNotReadyError,
)
from hara.api.runner import ApiTurnRunner, new_turn_id
from hara.api.schemas import PostMessageRequest, PostMessageResponse, TurnOut
from hara.api.sse import event_stream

router = APIRouter(prefix="/threads", tags=["turns"], dependencies=[Depends(require_auth)])

_ORCH_UNAVAILABLE_MSG = (
    "Orchestrator unavailable; check provider credentials and run `hara doctor`."
)


@router.post(
    "/{thread_id}/messages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PostMessageResponse,
)
async def post_message(
    thread_id: str,
    req: PostMessageRequest,
    request: Request,
) -> PostMessageResponse:
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
    if await store.get_thread(thread_id) is None:
        raise ThreadNotFoundError(f"Thread {thread_id} not found")

    bus = request.app.state.bus
    registry = request.app.state.registry
    orchestrator = request.app.state.orchestrator

    turn_id = new_turn_id()
    started_at = time.time()
    # Pre-create the turn row so events can FK to it during the run.
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
    task.add_done_callback(lambda _t: registry.unregister(turn_id))

    return PostMessageResponse(
        turn_id=turn_id,
        thread_id=thread_id,
        status="running",
        started_at=started_at,
    )


@router.get("/{thread_id}/turns", response_model=list[TurnOut])
async def list_turns(thread_id: str, request: Request, limit: int = 20) -> list[TurnOut]:
    store = request.app.state.store
    if await store.get_thread(thread_id) is None:
        raise ThreadNotFoundError(f"Thread {thread_id} not found")
    rows = await store.list_turns(thread_id=thread_id, limit=limit)
    return [_row_to_turn_out(r) for r in rows]


@router.get("/{thread_id}/turns/{turn_id}", response_model=TurnOut)
async def get_turn(thread_id: str, turn_id: str, request: Request) -> TurnOut:
    store = request.app.state.store
    rows = await store.list_turns(thread_id=thread_id, limit=1000)
    matching = next((r for r in rows if r["turn_id"] == turn_id), None)
    if matching is None:
        raise TurnNotFoundError(f"Turn {turn_id} not found")
    if matching["status"] == "running":
        raise TurnNotReadyError("turn still running")
    return _row_to_turn_out(matching)


@router.get("/{thread_id}/turns/{turn_id}/events")
async def turn_events(
    thread_id: str,
    turn_id: str,
    request: Request,
) -> StreamingResponse:
    store = request.app.state.store
    bus = request.app.state.bus

    rows = await store.list_turns(thread_id=thread_id, limit=1000)
    turn_row = next((r for r in rows if r["turn_id"] == turn_id), None)
    if turn_row is None:
        raise TurnNotFoundError(f"Turn {turn_id} not found")

    # Subscribe FIRST so any event emitted after this point is queued
    # for us — guarantees no event slips between replay snapshot and tail
    # subscription. Then read the persisted snapshot. Any overlap (events
    # both in replay and on the queue) is deduped by ``seq`` in
    # ``event_stream``.
    queue = bus.subscribe(turn_id)
    replay = await store.list_events(turn_id=turn_id)
    max_replay_seq = max((int(r["seq"]) for r in replay), default=0)

    # If the turn is already terminal, ``close_turn`` has already published
    # its sentinel to prior (gone) subscribers — our fresh queue will never
    # receive a ``None``. Skip the live tail to avoid hanging forever.
    is_terminal = turn_row["status"] in ("completed", "failed", "canceled")

    async def _gen() -> AsyncIterator[bytes]:
        try:
            async for chunk in event_stream(
                replay=replay,
                queue=queue,
                dedupe_seq=max_replay_seq,
                close_after_replay=is_terminal,
            ):
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


@router.delete("/{thread_id}/turns/{turn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_turn(thread_id: str, turn_id: str, request: Request) -> None:
    """Cancel an in-flight turn. Idempotent for already-completed turns
    (returns 204 anyway). Spec §6 'Cancelamento'."""
    store = request.app.state.store
    rows = await store.list_turns(thread_id=thread_id, limit=1000)
    if not any(r["turn_id"] == turn_id for r in rows):
        raise TurnNotFoundError(f"Turn {turn_id} not found")
    registry = request.app.state.registry
    # cancel() returns False if task not in registry or already done — fine.
    registry.cancel(turn_id)


def _row_to_turn_out(row: dict[str, Any]) -> TurnOut:
    return TurnOut(
        turn_id=row["turn_id"],
        thread_id=row["thread_id"],
        status=row["status"],
        question=row["question"],
        answer=row["answer"] or "",
        citations=row.get("citations") or [],
        metadata=row.get("metadata") or {},
        started_at=float(row["started_at"]),
        completed_at=float(row["completed_at"]) if row.get("completed_at") else None,
        canceled_at=float(row["canceled_at"]) if row.get("canceled_at") else None,
    )
