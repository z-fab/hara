"""Tests for FastAPI startup wiring + TTL cleanup."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hara.api.app import create_app
from hara.api.events import EventBus
from hara.api.turns import TurnRegistry
from hara.config.schemas import SessionStoreSQLiteConfig
from tests.unit.api.test_app_factory import _minimal_settings  # pyright: ignore[reportPrivateUsage]


def _settings_with_session_dsn(tmp_path: Path):
    s = _minimal_settings()
    s.connectors.session_store = SessionStoreSQLiteConfig(
        type="sqlite",
        path=tmp_path / "sess.db",
    )
    return s


def test_lifespan_initializes_store_bus_registry_orchestrator(tmp_path: Path) -> None:
    settings = _settings_with_session_dsn(tmp_path)
    app = create_app(settings)
    with TestClient(app):
        assert hasattr(app.state, "store")
        assert hasattr(app.state, "bus")
        assert hasattr(app.state, "registry")
        assert hasattr(app.state, "orchestrator")
        assert isinstance(app.state.bus, EventBus)
        assert isinstance(app.state.registry, TurnRegistry)


def test_lifespan_initializes_session_store_tables(tmp_path: Path) -> None:
    """SessionStore.initialize() ran during lifespan → list_threads works."""
    settings = _settings_with_session_dsn(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        r = client.get("/threads", headers={"Authorization": "Bearer test-token"})
        assert r.status_code == 200
        assert r.json() == []
