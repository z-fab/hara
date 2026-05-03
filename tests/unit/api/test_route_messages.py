"""Tests for POST /threads/{id}/messages — async turn kick-off."""

from __future__ import annotations

import time
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
    """Mocks Orchestrator.run_turn — finalizes the pre-created turn row.

    Mirrors the real orchestrator's API path (Codex review #2 P1): when the
    caller passes ``turn_id`` (i.e. has already pre-created the row), the
    orchestrator finalizes that row via ``update_turn_result`` rather than
    inserting a duplicate. Without this fidelity the runner's caller would
    see ``status='running'`` forever even though the run completed.
    """

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
        if turn_id is not None:
            await self._store.update_turn_result(
                turn_id=turn_id,
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
        # Lifespan may have set orchestrator=None (no API key) and re-built
        # store on its own DSN; override both with our test fakes AFTER
        # lifespan ran so they actually take effect for the requests.
        store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
        await store.initialize()
        app.state.store = store
        app.state.orchestrator = _FakeOrch(store)
        yield tc


def test_post_message_returns_202_running(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.post(
        f"/threads/{tid}/messages",
        json={"message": "Qual a produção?"},
        headers=_AUTH,
    )
    assert r.status_code == 202
    body = r.json()
    assert body["turn_id"].startswith("trn_")
    assert body["status"] == "running"


def test_post_message_to_unknown_thread_404(client: TestClient) -> None:
    r = client.post(
        "/threads/thr_nope/messages",
        json={"message": "x"},
        headers=_AUTH,
    )
    assert r.status_code == 404


def test_post_message_validates_max_length(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.post(
        f"/threads/{tid}/messages",
        json={"message": "x" * 9000},
        headers=_AUTH,
    )
    assert r.status_code == 422


def test_post_message_honors_configured_max_message_length(
    tmp_path: Path,
) -> None:
    """Codex review #4 P2: the route handler must enforce
    ``[api].max_message_length`` from settings — not the hardcoded
    schema cap. With a 10-char limit and a 50-char body we expect 422
    VALIDATION_ERROR with details exposing the configured cap."""
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
        # Need a non-None orchestrator for the route to reach the length
        # check (the config-error gate fires first otherwise).
        app.state.orchestrator = _FakeOrch(app.state.store)
        # Need a real thread so the 404 gate doesn't fire.
        create = tc.post("/threads", json={}, headers=_AUTH)
        tid = create.json()["thread_id"]
        r = tc.post(
            f"/threads/{tid}/messages",
            json={"message": "x" * 50},
            headers=_AUTH,
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert body["error"]["details"]["max_message_length"] == 10
        assert body["error"]["details"]["received"] == 50


def test_post_message_requires_auth(client: TestClient) -> None:
    r = client.post(
        "/threads/x/messages",
        json={"message": "x"},
    )
    assert r.status_code == 401


def test_post_message_completes_turn_to_completed(client: TestClient) -> None:
    """Codex review #2 P1: after the runner finishes, the turn row created
    in POST /messages must reach status='completed' (not stuck on
    'running'). Pre-fix: orchestrator.run_turn was inserting a duplicate
    row under its own turn_id and the API's pre-created row never got
    flipped, so polling the original turn_id returned 425 forever."""
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]

    msg = client.post(
        f"/threads/{tid}/messages",
        json={"message": "ping"},
        headers=_AUTH,
    )
    assert msg.status_code == 202
    turn_id = msg.json()["turn_id"]

    # Wait briefly for the background runner task to finish. TestClient
    # runs the lifespan on a worker thread; the background task is
    # scheduled on its loop. A few short polls beat a single long sleep.
    deadline = time.time() + 2.0
    detail_status = 0
    while time.time() < deadline:
        detail = client.get(
            f"/threads/{tid}/turns/{turn_id}",
            headers=_AUTH,
        )
        detail_status = detail.status_code
        if detail_status == 200:
            body = detail.json()
            assert body["status"] == "completed"
            assert body["turn_id"] == turn_id
            assert body["answer"] == "resp"
            break
        time.sleep(0.05)
    else:
        msg = f"turn never completed; last detail status={detail_status}"
        raise AssertionError(msg)

    # And the thread has exactly one turn — no duplicate row.
    listing = client.get(f"/threads/{tid}/turns", headers=_AUTH)
    assert listing.status_code == 200
    assert len(listing.json()) == 1
