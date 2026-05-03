"""Tests for /health and /version routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hara import __version__
from hara.api.app import create_app
from tests.unit.api.test_app_factory import _minimal_settings


def _client() -> TestClient:
    return TestClient(create_app(_minimal_settings()))


def test_health_returns_200_without_auth() -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_no_auth_required() -> None:
    """No Authorization header — still 200."""
    r = _client().get("/health")
    assert r.status_code == 200


def test_version_returns_current_package_version() -> None:
    r = _client().get(
        "/version",
        headers={"Authorization": "Bearer test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == __version__


def test_version_requires_auth() -> None:
    r = _client().get("/version")
    assert r.status_code == 401
