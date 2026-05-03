"""API request/response Pydantic models.

Mirror :class:`hara.agent.orchestrator.TurnResult` for the
``InvokeResponse`` / SSE ``final`` payloads — the wire format is the
authoritative shape; ``TurnResult`` is what the orchestrator emits and
the API forwards as-is.

Spec §6 ``Schemas principais``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Defensive hard cap to reject pathological payloads at parse time. The
# operator-configured limit lives in ``Settings.api.max_message_length``
# and is enforced by the route handlers (see
# :class:`hara.api.errors.MessageTooLongError`); this 100_000-char
# ceiling is only here so a 10 MB message body cannot reach the route
# layer. Bumped from the spec default of 8000 (Codex review #4 P2) so
# operators can configure a higher per-deployment limit.
_MAX_MESSAGE_LENGTH = 100_000


# ---------- Threads ----------


class CreateThreadRequest(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None


class CreateThreadResponse(BaseModel):
    thread_id: str
    created_at: float


class ThreadOut(BaseModel):
    thread_id: str
    title: str | None = None
    created_at: float
    last_active_at: float


# ---------- Messages / Turns ----------


class PostMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LENGTH)


class PostMessageResponse(BaseModel):
    turn_id: str
    thread_id: str
    status: Literal["running", "completed", "failed", "canceled"]
    started_at: float


class CitationOut(BaseModel):
    evidence_id: int
    kind: Literal["sql", "text"]
    source: str
    section: str = ""
    snippet: str = ""


class TurnOut(BaseModel):
    turn_id: str
    thread_id: str
    status: Literal["running", "completed", "failed", "canceled"]
    question: str
    answer: str = ""
    citations: list[CitationOut] = Field(default_factory=list[CitationOut])
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])
    started_at: float
    completed_at: float | None = None
    canceled_at: float | None = None


# ---------- Convenience: invoke / stream ----------


class InvokeRequest(BaseModel):
    thread_id: str | None = None
    message: str = Field(min_length=1, max_length=_MAX_MESSAGE_LENGTH)


class InvokeResponse(BaseModel):
    """Same shape as the SSE ``final`` event payload."""

    turn_id: str
    thread_id: str
    answer: str
    citations: list[CitationOut] = Field(default_factory=list[CitationOut])
    metadata: dict[str, Any] = Field(default_factory=dict[str, Any])
