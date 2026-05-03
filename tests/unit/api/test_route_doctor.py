"""Tests for GET /doctor."""

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
async def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    settings = _minimal_settings()
    settings.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    monkeypatch.chdir(tmp_path)
    app = create_app(settings)
    store = SessionStore(dsn=f"sqlite:///{tmp_path}/sess.db")
    await store.initialize()
    app.state.store = store
    with TestClient(app) as tc:
        yield tc


def test_doctor_returns_status_and_checks(client: TestClient) -> None:
    r = client.get("/doctor", headers=_AUTH)
    # Status code may be 200 or 503 depending on warnings.
    assert r.status_code in (200, 503)
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert "checks" in body
    assert isinstance(body["checks"], list)
    assert len(body["checks"]) > 0
    # Every check has the structure: {check, status, detail}
    for c in body["checks"]:
        assert "check" in c
        assert "status" in c
        assert "detail" in c


def test_doctor_requires_auth(client: TestClient) -> None:
    r = client.get("/doctor")
    assert r.status_code == 401
