"""Tests for the global error → JSON handler."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hara.api.app import _register_error_handlers  # pyright: ignore[reportPrivateUsage]
from hara.api.errors import (
    ConfigError,
    HaraAPIError,
    LLMProviderError,
    SQLValidationError,
    ThreadNotFoundError,
    TurnNotFoundError,
    TurnNotReadyError,
)


def _app_raising(exc_to_raise: HaraAPIError) -> FastAPI:
    app = FastAPI()
    _register_error_handlers(app)

    @app.get("/raise")
    def _raise() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        raise exc_to_raise

    return app


def test_thread_not_found_404() -> None:
    client = TestClient(_app_raising(ThreadNotFoundError("Thread thr_x not found")))
    r = client.get("/raise")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "THREAD_NOT_FOUND"
    assert "Thread thr_x" in body["error"]["message"]


def test_turn_not_ready_425() -> None:
    client = TestClient(_app_raising(TurnNotReadyError("running")))
    r = client.get("/raise")
    assert r.status_code == 425
    assert r.json()["error"]["code"] == "TURN_NOT_READY"


def test_turn_not_found_404() -> None:
    r = TestClient(_app_raising(TurnNotFoundError("x"))).get("/raise")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "TURN_NOT_FOUND"


def test_llm_provider_error_502_with_details() -> None:
    err = LLMProviderError(
        "rate limited",
        details={"provider": "openai", "retryable": True},
    )
    r = TestClient(_app_raising(err)).get("/raise")
    assert r.status_code == 502
    body = r.json()
    assert body["error"]["code"] == "LLM_PROVIDER_ERROR"
    assert body["error"]["details"] == {"provider": "openai", "retryable": True}


def test_config_error_500() -> None:
    r = TestClient(_app_raising(ConfigError("missing structured.yaml"))).get("/raise")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INVALID_CONFIG"


def test_sql_validation_error_502() -> None:
    r = TestClient(_app_raising(SQLValidationError("DML inside query"))).get("/raise")
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "SQL_VALIDATION_ERROR"


def test_error_body_always_has_details_dict() -> None:
    """Even when caller didn't pass details, the JSON has details: {}."""
    r = TestClient(_app_raising(ThreadNotFoundError("x"))).get("/raise")
    assert r.json()["error"]["details"] == {}
