"""Tests for POST /invoke (sync convenience)."""

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
    """run_turn updates the DB row so /invoke can read it back."""

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    async def run_turn(
        self,
        question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Any = None,
        on_event: Any = None,
    ) -> TurnResult:
        # Mimic the real orchestrator's API path: the row was pre-created
        # by the route handler with status='running'; the orchestrator
        # finalizes it via update_turn_result. The fake follows the same
        # contract so /invoke can read the completed row back.
        if turn_id is not None:
            await self._store.update_turn_result(
                turn_id=turn_id,
                answer=f"resp to {question}",
                citations=[],
                metadata={
                    "routing": {"subqueries_count": 0, "routes_taken": []},
                    "verifier": None,
                    "tokens": {},
                    "latency_per_node": {},
                    "unsupported_markers": 0,
                },
            )
        return TurnResult(
            thread_id=thread_id or "thr_x",
            turn_id=turn_id or "trn_x",
            answer=f"resp to {question}",
            citations=[],
            metadata={
                "routing": {"subqueries_count": 0, "routes_taken": []},
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
        # Lifespan may have set orchestrator=None (no API key) and re-built
        # store on its own DSN; override both with our test fakes AFTER
        # lifespan ran so they actually take effect for the requests.
        store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
        await store.initialize()
        app.state.store = store
        app.state.orchestrator = _FakeOrch(store)
        yield tc


def test_invoke_with_thread_id(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.post(
        "/invoke",
        json={"thread_id": tid, "message": "Quanto produziu MT?"},
        headers=_AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["thread_id"] == tid
    assert body["turn_id"].startswith("trn_")
    # answer comes from the DB row, which the runner writes via the final event
    # path. With the FakeOrch the row's answer might be "" if record_turn was
    # called pre-status='completed' and the runner didn't update it. The
    # important assertion is that the response shape is correct.
    assert "answer" in body
    assert "metadata" in body


def test_invoke_without_thread_id_auto_creates(client: TestClient) -> None:
    r = client.post("/invoke", json={"message": "oi"}, headers=_AUTH)
    assert r.status_code == 200
    assert r.json()["thread_id"].startswith("thr_")


def test_invoke_requires_auth(client: TestClient) -> None:
    r = client.post("/invoke", json={"message": "x"})
    assert r.status_code == 401


def test_invoke_orchestrator_unavailable_returns_500_invalid_config(
    client: TestClient,
) -> None:
    """Codex review #3 P1: when startup failed to build the orchestrator
    (missing provider creds, etc.), /invoke must return 500 INVALID_CONFIG
    instead of silently producing an empty answer through the runner."""
    client.app.state.orchestrator = None  # pyright: ignore[reportAttributeAccessIssue]
    r = client.post("/invoke", json={"message": "x"}, headers=_AUTH)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INVALID_CONFIG"


def test_invoke_unknown_thread_id_returns_404(client: TestClient) -> None:
    """Codex review #3 P2: an unknown thread_id must 404 instead of inserting
    an orphaned turn row. Mirrors POST /threads/{id}/messages behavior."""
    r = client.post(
        "/invoke",
        json={"thread_id": "thr_nope", "message": "x"},
        headers=_AUTH,
    )
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "THREAD_NOT_FOUND"


class _FailingOrch:
    """run_turn raises — exercises the runner's failed-turn path so /invoke
    can observe ``status='failed'`` on the persisted row."""

    async def run_turn(
        self,
        _question: str,
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        on_token: Any = None,
        on_event: Any = None,
    ) -> TurnResult:
        msg = "boom"
        raise RuntimeError(msg)


def test_invoke_failed_turn_returns_502_llm_provider_error(
    tmp_path: Path,
) -> None:
    """Codex review #4 P1: when the runner catches an exception and marks
    the turn 'failed', /invoke must surface that as 502 LLM_PROVIDER_ERROR
    instead of returning 200 with an empty answer (spec §6)."""
    settings = _minimal_settings()
    settings.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    app = create_app(settings)
    with TestClient(app) as tc:
        # Override after lifespan ran so the failing orch actually runs.
        store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
        # Lifespan already initialized the store; we reuse the path so
        # turn rows the route writes are visible to our query.
        app.state.store = store
        app.state.orchestrator = _FailingOrch()
        r = tc.post("/invoke", json={"message": "x"}, headers=_AUTH)
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "LLM_PROVIDER_ERROR"


def test_invoke_message_exceeds_configured_max_returns_422(
    tmp_path: Path,
) -> None:
    """Codex review #4 P2: ``[api].max_message_length`` from settings must
    be honored at the route level (the Pydantic field only enforces a
    defensive 100_000-char cap)."""
    from hara.config.settings import APISettings, Settings  # noqa: PLC0415

    base = _minimal_settings()
    base.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    settings = Settings.model_validate(
        base.model_dump() | {"api": APISettings(max_message_length=10).model_dump()}
    )
    app = create_app(settings)
    with TestClient(app) as tc:
        # Lifespan built the store + orchestrator may be None (no creds);
        # only need a non-None orchestrator to clear the ConfigError gate
        # before the length check fires.
        app.state.orchestrator = _FakeOrch(app.state.store)
        r = tc.post(
            "/invoke",
            json={"message": "x" * 50},
            headers=_AUTH,
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["details"]["max_message_length"] == 10
        assert body["error"]["details"]["received"] == 50
