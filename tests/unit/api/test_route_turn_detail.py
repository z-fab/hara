"""Tests for GET turns list + GET turn detail endpoints."""

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
    async def run_turn(
        self,
        _question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Any = None,
        on_event: Any = None,
    ) -> TurnResult:
        return TurnResult(
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
        # lifespan's None orchestrator (no API key in test env) wins and
        # post_message now returns 500 INVALID_CONFIG (Codex review #3 P1).
        store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
        await store.initialize()
        app.state.store = store
        app.state.orchestrator = _FakeOrch()
        yield tc


def test_list_turns_for_thread(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    # POST 2 messages — list_turns reads from DB, which sees both rows
    # since record_turn inserts synchronously inside post_message before
    # the runner task is scheduled.
    client.post(f"/threads/{tid}/messages", json={"message": "q1"}, headers=_AUTH)
    client.post(f"/threads/{tid}/messages", json={"message": "q2"}, headers=_AUTH)
    r = client.get(f"/threads/{tid}/turns", headers=_AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_list_turns_unknown_thread_404(client: TestClient) -> None:
    r = client.get("/threads/thr_nope/turns", headers=_AUTH)
    assert r.status_code == 404


def test_get_turn_detail_returns_200_or_425(client: TestClient) -> None:
    """Either status is fine — turn may have raced to completion."""
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    msg = client.post(f"/threads/{tid}/messages", json={"message": "x"}, headers=_AUTH)
    turn_id = msg.json()["turn_id"]
    r = client.get(f"/threads/{tid}/turns/{turn_id}", headers=_AUTH)
    assert r.status_code in (200, 425)


def test_get_turn_detail_404(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.get(f"/threads/{tid}/turns/trn_nope", headers=_AUTH)
    assert r.status_code == 404
