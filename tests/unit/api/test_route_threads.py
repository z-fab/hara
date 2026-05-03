"""Tests for /threads CRUD routes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hara.api.app import create_app
from hara.config.schemas import SessionStoreSQLiteConfig
from hara.services.session_store import SessionStore
from tests.unit.api.test_app_factory import _minimal_settings  # pyright: ignore[reportPrivateUsage]

_AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture
async def client(tmp_path: Path) -> Iterator[TestClient]:
    """The threads routes need a SessionStore on app.state.

    We pre-attach one initialized to a sqlite tmpfile (lifespan also
    sets store, but tests can avoid the full lifespan by providing it
    directly)."""
    settings = _minimal_settings()
    settings.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    app = create_app(settings)
    store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await store.initialize()
    app.state.store = store
    with TestClient(app) as tc:
        yield tc


def test_post_thread_returns_201_with_id(client: TestClient) -> None:
    r = client.post("/threads", json={"title": "my thread"}, headers=_AUTH)
    assert r.status_code == 201
    body = r.json()
    assert body["thread_id"].startswith("thr_")
    assert "created_at" in body


def test_post_thread_no_body_creates_anonymous(client: TestClient) -> None:
    r = client.post("/threads", json={}, headers=_AUTH)
    assert r.status_code == 201


def test_get_threads_lists_existing(client: TestClient) -> None:
    client.post("/threads", json={"title": "a"}, headers=_AUTH)
    client.post("/threads", json={"title": "b"}, headers=_AUTH)
    r = client.get("/threads", headers=_AUTH)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_get_thread_by_id_200(client: TestClient) -> None:
    create = client.post("/threads", json={"title": "t"}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.get(f"/threads/{tid}", headers=_AUTH)
    assert r.status_code == 200
    assert r.json()["thread_id"] == tid


def test_get_thread_by_id_404(client: TestClient) -> None:
    r = client.get("/threads/thr_nonexistent", headers=_AUTH)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "THREAD_NOT_FOUND"


def test_delete_thread_204(client: TestClient) -> None:
    create = client.post("/threads", json={}, headers=_AUTH)
    tid = create.json()["thread_id"]
    r = client.delete(f"/threads/{tid}", headers=_AUTH)
    assert r.status_code == 204
    assert client.get(f"/threads/{tid}", headers=_AUTH).status_code == 404


def test_delete_thread_404_unknown(client: TestClient) -> None:
    r = client.delete("/threads/thr_nope", headers=_AUTH)
    assert r.status_code == 404


def test_threads_require_auth(client: TestClient) -> None:
    assert client.post("/threads", json={}).status_code == 401
    assert client.get("/threads").status_code == 401
