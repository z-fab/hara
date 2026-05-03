"""Tests for API request/response schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hara.api.schemas import (
    CitationOut,
    CreateThreadRequest,
    CreateThreadResponse,
    InvokeRequest,
    InvokeResponse,
    PostMessageRequest,
    PostMessageResponse,
    ThreadOut,
    TurnOut,
)


def test_module_exports_smoke() -> None:
    """All public schemas are importable and instantiable with minimal args."""
    assert CreateThreadResponse(thread_id="thr_x", created_at=1.0).thread_id == "thr_x"
    assert (
        PostMessageResponse(
            turn_id="trn_x",
            thread_id="thr_x",
            status="running",
            started_at=1.0,
        ).status
        == "running"
    )
    assert CitationOut(evidence_id=1, kind="sql", source="s").evidence_id == 1


def test_create_thread_request_optional_fields() -> None:
    r = CreateThreadRequest()
    assert r.title is None
    assert r.metadata is None


def test_create_thread_request_validates_metadata_dict() -> None:
    r = CreateThreadRequest(title="t", metadata={"k": "v"})
    assert r.metadata == {"k": "v"}


def test_post_message_request_message_required() -> None:
    with pytest.raises(ValidationError):
        PostMessageRequest()  # pyright: ignore[reportCallIssue]


def test_post_message_request_message_max_length() -> None:
    """Defensive 100_000-char hard cap (Codex review #4 P2). The
    operator-configured ``[api].max_message_length`` (default 8000 per
    spec §6) is enforced by the route handlers via
    :class:`MessageTooLongError`; the Pydantic field only rejects
    pathologically large payloads."""
    with pytest.raises(ValidationError):
        PostMessageRequest(message="x" * 100_001)


def test_invoke_request_thread_id_optional() -> None:
    r = InvokeRequest(message="oi")
    assert r.thread_id is None
    r2 = InvokeRequest(thread_id="thr_x", message="oi")
    assert r2.thread_id == "thr_x"


def test_invoke_response_round_trip_via_dict() -> None:
    """InvokeResponse should accept the same dict shape we'll feed from
    Orchestrator.TurnResult.metadata + answer + citations."""
    payload = {
        "turn_id": "trn_x",
        "thread_id": "thr_x",
        "answer": "MT produziu 100t <ref:1>.",
        "citations": [
            {
                "evidence_id": 1,
                "kind": "sql",
                "source": "producao",
                "section": "",
                "snippet": "MT, 100",
            },
        ],
        "metadata": {
            "routing": {"subqueries_count": 1, "routes_taken": ["sql"]},
            "verifier": None,
            "tokens": {"input": 10, "output": 5, "total": 15},
            "latency_per_node": {"planner": 0.1},
            "unsupported_markers": 0,
        },
    }
    resp = InvokeResponse.model_validate(payload)
    assert resp.answer == payload["answer"]
    assert len(resp.citations) == 1
    assert resp.citations[0].evidence_id == 1


def test_turn_out_schema() -> None:
    t = TurnOut(
        turn_id="trn_x",
        thread_id="thr_x",
        status="completed",
        question="q",
        answer="a",
        citations=[],
        metadata={},
        started_at=1.0,
        completed_at=2.0,
    )
    assert t.status == "completed"


def test_thread_out_schema() -> None:
    t = ThreadOut(thread_id="thr_x", title=None, created_at=1.0, last_active_at=2.0)
    assert t.thread_id == "thr_x"
