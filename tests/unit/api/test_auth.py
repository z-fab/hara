"""Tests for Bearer auth dependency."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from hara.api.deps import require_auth
from hara.config.settings import Settings
from tests.unit.api.test_app_factory import _minimal_settings


def _app_with_auth() -> FastAPI:
    """Tiny app exposing a single protected route. Used by all auth tests."""
    settings = _minimal_settings()
    # Override token for predictability
    settings = Settings.model_validate(settings.model_dump() | {"auth": {"token": "secret-xyz"}})
    app = FastAPI()
    app.state.settings = settings
    # Register error handlers so AuthError → 401 JSON, not 500
    from hara.api.app import _register_error_handlers  # noqa: PLC0415

    _register_error_handlers(app)

    @app.get("/protected")
    def _protected(_: Any = Depends(require_auth)) -> dict[str, str]:  # noqa: B008
        return {"ok": "true"}

    return app


def test_missing_authorization_header_401() -> None:
    client = TestClient(_app_with_auth())
    r = client.get("/protected")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "INVALID_TOKEN"


def test_wrong_scheme_401() -> None:
    client = TestClient(_app_with_auth())
    r = client.get("/protected", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_wrong_token_401() -> None:
    client = TestClient(_app_with_auth())
    r = client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_correct_token_200() -> None:
    client = TestClient(_app_with_auth())
    r = client.get("/protected", headers={"Authorization": "Bearer secret-xyz"})
    assert r.status_code == 200
    assert r.json() == {"ok": "true"}
