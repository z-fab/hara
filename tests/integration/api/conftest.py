"""Shared fixtures for API integration tests.

Uses httpx.AsyncClient against the real FastAPI app (lifespan triggers).
A FakeOrch is injected post-startup so tests don't need real LLM keys.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from hara.agent.orchestrator import TurnResult
from hara.api.app import create_app
from hara.config.schemas import (
    MemorySQLConfig,
    MemoryVectorConfig,
    SessionStoreSQLiteConfig,
)
from hara.config.settings import (
    AuthSettings,
    ConnectorsSettings,
    EmbeddingsSettings,
    ModelRef,
    ModelsSettings,
    Settings,
)
from hara.services.session_store import SessionStore

_AUTH = {"Authorization": "Bearer integration-token"}


def _settings(tmp_path: Path) -> Settings:
    """Memory SQL + memory vector + sqlite session-store on tmp_path."""
    return Settings(
        models=ModelsSettings(
            hard=ModelRef(provider="openai", model="gpt-5"),
            soft=ModelRef(provider="openai", model="gpt-5-mini"),
        ),
        embeddings=EmbeddingsSettings(provider="openai", model="text-embedding-3-small"),
        connectors=ConnectorsSettings(
            sql=MemorySQLConfig(type="memory"),
            vector=MemoryVectorConfig(type="memory"),
            session_store=SessionStoreSQLiteConfig(
                type="sqlite",
                path=tmp_path / "sess.db",
            ),
        ),
        auth=AuthSettings(token="integration-token"),  # noqa: S106
    )


class _FakeOrch:
    """Mocks Orchestrator.run_turn — emits state events + persists turn row."""

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
        if on_event is not None:
            await on_event({"event": "state", "data": {"node": "planner", "status": "started"}})
            await on_event({"event": "state", "data": {"node": "planner", "status": "completed"}})
            await on_event({"event": "state", "data": {"node": "synthesizer", "status": "started"}})
            for tok in ("Hello, ", "world."):
                if on_token is not None:
                    on_token(tok)
                await on_event({"event": "token", "data": {"text": tok}})
            await on_event(
                {"event": "state", "data": {"node": "synthesizer", "status": "completed"}}
            )
        result = TurnResult(
            thread_id=thread_id or "thr_x",
            turn_id=turn_id or "trn_x",
            answer=f"Hello, world. (q: {question})",
            citations=[],
            metadata={
                "routing": {"subqueries_count": 0, "routes_taken": []},
                "verifier": None,
                "tokens": {"input": 0, "output": 2, "total": 2},
                "latency_per_node": {"planner": 0.01, "synthesizer": 0.02},
                "unsupported_markers": 0,
            },
        )
        if turn_id is not None:
            await self._store.update_turn_result(
                turn_id=turn_id,
                answer=result.answer,
                citations=result.citations,
                metadata=result.metadata,
                status="completed",
            )
        return result


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    """httpx.AsyncClient against the real lifespan-initialized app.

    Yields (client, app) so tests can read app.state.store directly.
    """
    settings = _settings(tmp_path)
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)

    # Combined async with: AsyncClient holds the ASGI transport open while
    # lifespan_context drives FastAPI startup/shutdown around it.
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers=_AUTH,
        ) as ac,
        app.router.lifespan_context(app),
    ):
        # Override the (likely-None) orchestrator with a fake — the real
        # build_orchestrator_from_settings would need LLM API keys.
        app.state.orchestrator = _FakeOrch(app.state.store)
        yield ac, app
