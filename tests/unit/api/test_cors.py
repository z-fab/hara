"""Tests for CORS middleware (registered when [api].cors.allowed_origins set)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hara.api.app import create_app
from hara.config.settings import APISettings, CorsSettings, Settings
from tests.unit.api.test_app_factory import _minimal_settings  # pyright: ignore[reportPrivateUsage]


def _settings_with_cors(*origins: str) -> Settings:
    base = _minimal_settings()
    return Settings.model_validate(
        base.model_dump()
        | {
            "api": APISettings(
                cors=CorsSettings(allowed_origins=list(origins)),
            ).model_dump()
        }
    )


def test_no_cors_middleware_by_default() -> None:
    """Default settings (allowed_origins=[]) — no CORS headers in response."""
    app = create_app(_minimal_settings())
    client = TestClient(app)
    r = client.get("/health", headers={"Origin": "https://example.com"})
    assert r.status_code == 200
    # Without CORS middleware, no Access-Control-Allow-Origin header
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_cors_middleware_when_origins_configured() -> None:
    """With cors.allowed_origins set — preflight returns Access-Control headers."""
    settings = _settings_with_cors("https://example.com")
    app = create_app(settings)
    client = TestClient(app)
    # Preflight OPTIONS
    r = client.options(
        "/health",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # CORS middleware responds 200 to preflight
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "https://example.com"


def test_cors_rejects_unconfigured_origin() -> None:
    """Origin not in allowed_origins — no Access-Control-Allow-Origin echoed."""
    settings = _settings_with_cors("https://example.com")
    app = create_app(settings)
    client = TestClient(app)
    r = client.get(
        "/health",
        headers={"Origin": "https://malicious.com"},
    )
    assert r.status_code == 200
    # CORS middleware does NOT include the allow-origin header for unallowed origins.
    assert r.headers.get("access-control-allow-origin") != "https://malicious.com"
