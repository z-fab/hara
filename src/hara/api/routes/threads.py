"""Thread CRUD endpoints. Spec §6 'CORE'."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from hara.api.deps import require_auth
from hara.api.errors import ThreadNotFoundError
from hara.api.schemas import (
    CreateThreadRequest,
    CreateThreadResponse,
    ThreadOut,
)
from hara.services.session_store import SessionStore

router = APIRouter(prefix="/threads", tags=["threads"], dependencies=[Depends(require_auth)])


def _get_store(request: Request) -> SessionStore:
    """Pull the SessionStore from app.state. Set during startup or test fixture."""
    store: SessionStore = request.app.state.store
    return store


@router.post("", status_code=status.HTTP_201_CREATED, response_model=CreateThreadResponse)
async def create_thread(
    req: CreateThreadRequest,
    request: Request,
) -> CreateThreadResponse:
    store = _get_store(request)
    thread_id = await store.create_thread(title=req.title, metadata=req.metadata)
    row = await store.get_thread(thread_id)
    assert row is not None
    return CreateThreadResponse(thread_id=thread_id, created_at=float(row["created_at"]))


@router.get("", response_model=list[ThreadOut])
async def list_threads(request: Request, limit: int = 50) -> list[ThreadOut]:
    store = _get_store(request)
    rows = await store.list_threads(limit=limit)
    return [
        ThreadOut(
            thread_id=r["thread_id"],
            title=r["title"],
            created_at=float(r["created_at"]),
            last_active_at=float(r["last_active_at"]),
        )
        for r in rows
    ]


@router.get("/{thread_id}", response_model=ThreadOut)
async def get_thread(thread_id: str, request: Request) -> ThreadOut:
    store = _get_store(request)
    row = await store.get_thread(thread_id)
    if row is None:
        raise ThreadNotFoundError(f"Thread {thread_id} not found")
    return ThreadOut(
        thread_id=row["thread_id"],
        title=row["title"],
        created_at=float(row["created_at"]),
        last_active_at=float(row["last_active_at"]),
    )


@router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(thread_id: str, request: Request) -> None:
    store = _get_store(request)
    deleted = await store.delete_thread(thread_id)
    if not deleted:
        raise ThreadNotFoundError(f"Thread {thread_id} not found")
