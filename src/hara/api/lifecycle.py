"""FastAPI startup/shutdown hooks for HARA.

Wires the per-process state attached to ``app.state``:
  - ``store``: SessionStore (initialized; tables created if missing).
  - ``bus``: EventBus.
  - ``registry``: TurnRegistry.
  - ``orchestrator``: Orchestrator (built via build_orchestrator_from_settings).

At startup, also runs TTL cleanup (spec §17 critério 21).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from hara.agent.setup import build_orchestrator_from_settings
from hara.api.events import EventBus
from hara.api.turns import TurnRegistry
from hara.services.session_store import SessionStore, derive_session_dsn

if TYPE_CHECKING:
    from fastapi import FastAPI

log = logging.getLogger(__name__)

_TTL_FALLBACK_DAYS = 7  # spec §6 default
_SECONDS_PER_DAY = 86400


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = app.state.settings

    store = SessionStore(dsn=derive_session_dsn(settings))
    await store.initialize()

    # TTL cleanup: drop stale turns/events older than ttl_days.
    ttl_days = getattr(settings.connectors.session_store, "ttl_days", _TTL_FALLBACK_DAYS)
    if ttl_days is not None and ttl_days > 0:
        await store.cleanup_expired(older_than_seconds=ttl_days * _SECONDS_PER_DAY)

    bus = EventBus()
    registry = TurnRegistry()

    app.state.store = store
    app.state.bus = bus
    app.state.registry = registry

    # Building the orchestrator resolves LLM/embeddings providers, which can
    # raise (e.g. missing API key). Don't take down the whole API for that:
    # CRUD routes (/threads, /health, /version) still work without an
    # orchestrator. The /messages, /invoke, /stream routes that *do* need
    # one will surface a clear 503 via deps.get_orchestrator.
    try:
        app.state.orchestrator = await build_orchestrator_from_settings(
            settings,
            data_dir=settings.paths.data_dir,
        )
    except Exception:
        log.exception("Orchestrator build failed at startup; running in degraded mode")
        app.state.orchestrator = None

    log.info("HARA API ready")
    try:
        yield
    finally:
        # Shutdown — cancel any in-flight turn tasks.
        registry.cancel_all()
