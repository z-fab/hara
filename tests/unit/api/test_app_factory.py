"""Tests for the FastAPI app factory."""

from __future__ import annotations

from hara.api.app import create_app
from hara.config.settings import Settings


def _minimal_settings() -> Settings:
    """Build a Settings instance suitable for tests — no real connectors needed
    at app-factory time. The factory must accept a Settings and return a FastAPI
    instance without performing any I/O."""
    from hara.config.schemas import MemorySQLConfig, MemoryVectorConfig  # noqa: PLC0415
    from hara.config.settings import (  # noqa: PLC0415
        AuthSettings,
        ConnectorsSettings,
        EmbeddingsSettings,
        ModelRef,
        ModelsSettings,
    )

    return Settings(
        models=ModelsSettings(
            hard=ModelRef(provider="openai", model="gpt-5"),
            soft=ModelRef(provider="openai", model="gpt-5-mini"),
        ),
        embeddings=EmbeddingsSettings(provider="openai", model="text-embedding-3-small"),
        connectors=ConnectorsSettings(
            sql=MemorySQLConfig(type="memory"),
            vector=MemoryVectorConfig(type="memory"),
        ),
        auth=AuthSettings(token="test-token"),  # noqa: S106
    )


def test_create_app_returns_fastapi_instance() -> None:
    from fastapi import FastAPI  # noqa: PLC0415

    app = create_app(_minimal_settings())
    assert isinstance(app, FastAPI)


def test_create_app_carries_settings_in_state() -> None:
    """The factory should attach the Settings to app.state so downstream
    dependencies (auth, orchestrator factory) can pull it."""
    settings = _minimal_settings()
    app = create_app(settings)
    assert app.state.settings is settings


def test_create_app_title_and_version() -> None:
    """Smoke check that OpenAPI metadata is set."""
    from hara import __version__  # noqa: PLC0415

    app = create_app(_minimal_settings())
    assert app.title == "HARA"
    assert app.version == __version__


def test_openapi_open_when_open_docs_true() -> None:
    """Default open_docs=true — /docs accessible without auth."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    settings = _minimal_settings()
    # Default APISettings already has open_docs=True
    app = create_app(settings)
    client = TestClient(app)
    r = client.get("/docs")
    assert r.status_code == 200


def test_openapi_locked_when_open_docs_false() -> None:
    """When operator sets open_docs=false (production behind public IP),
    /docs and /openapi.json must require Bearer."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from hara.config.settings import APISettings, Settings  # noqa: PLC0415

    base = _minimal_settings()
    settings = Settings.model_validate(
        base.model_dump() | {"api": APISettings(open_docs=False).model_dump()}
    )
    app = create_app(settings)
    client = TestClient(app)
    # Without auth — 401
    assert client.get("/openapi.json").status_code == 401
    # With auth — 200
    r = client.get("/openapi.json", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
