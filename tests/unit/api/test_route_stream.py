"""Tests for POST /stream — SSE convenience wrapper of /invoke."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hara.agent.orchestrator import TurnResult
from hara.api.app import create_app
from hara.config.schemas import SessionStoreSQLiteConfig
from hara.services.session_store import SessionStore
from tests.unit.api.test_app_factory import _minimal_settings  # pyright: ignore[reportPrivateUsage]

_AUTH = {"Authorization": "Bearer test-token"}


class _FakeOrch:
    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def run_turn(
        self,
        _question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Any = None,
        on_event: Any = None,
    ) -> TurnResult:
        # Emit some state events for the stream to forward.
        if on_event is not None:
            await on_event({"event": "state", "data": {"node": "planner", "status": "started"}})
            await on_event({"event": "state", "data": {"node": "planner", "status": "completed"}})
        result = TurnResult(
            thread_id=thread_id or "thr_x",
            turn_id=turn_id or "trn_x",
            answer="resp",
            citations=[],
            metadata={
                "routing": {},
                "verifier": None,
                "tokens": {},
                "latency_per_node": {},
                "unsupported_markers": 0,
            },
        )
        # Update the pre-existing turn row to completed (mirrors the real orch path)
        if turn_id is not None:
            await self._store.update_turn_result(
                turn_id=turn_id,
                answer=result.answer,
                citations=result.citations,
                metadata=result.metadata,
                status="completed",
            )
        return result


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[TestClient]:
    settings = _minimal_settings()
    settings.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    app = create_app(settings)
    with TestClient(app) as tc:
        # Override store + orchestrator AFTER lifespan ran, otherwise the
        # lifespan's None orchestrator (no API key in test env) wins.
        store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
        await store.initialize()
        app.state.store = store
        app.state.orchestrator = _FakeOrch(store)
        yield tc


def test_stream_returns_text_event_stream(client: TestClient) -> None:
    """Smoke: shape only — content-type + 200. Streaming body coverage in Phase F."""
    r = client.post("/stream", json={"message": "x"}, headers=_AUTH)
    # TestClient does not properly stream — it returns immediately with 200
    # and a body that may be partial. We just verify status + content-type.
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]


def test_stream_requires_auth(client: TestClient) -> None:
    r = client.post("/stream", json={"message": "x"})
    assert r.status_code == 401


def test_stream_validates_message_length(client: TestClient) -> None:
    r = client.post("/stream", json={"message": "x" * 9000}, headers=_AUTH)
    assert r.status_code == 422


def test_stream_orchestrator_unavailable_returns_500_invalid_config(
    client: TestClient,
) -> None:
    """Codex review #3 P1: when startup failed to build the orchestrator,
    /stream must return 500 INVALID_CONFIG instead of trying to run a turn
    against ``None``."""
    client.app.state.orchestrator = None  # pyright: ignore[reportAttributeAccessIssue]
    r = client.post("/stream", json={"message": "x"}, headers=_AUTH)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INVALID_CONFIG"


def test_stream_unknown_thread_id_returns_404(client: TestClient) -> None:
    """Codex review #3 P2: same orphan-turn protection as /invoke."""
    r = client.post(
        "/stream",
        json={"thread_id": "thr_nope", "message": "x"},
        headers=_AUTH,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "THREAD_NOT_FOUND"
