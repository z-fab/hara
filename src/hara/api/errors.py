"""Error model + global exception handlers.

Spec §6 "Erros": every error response follows the shape
``{"error": {"code": "INVALID_TOKEN", "message": "...", "details": {}}}``.

Exception classes here let route code raise typed errors; the global
handlers (registered in :mod:`hara.api.app`) convert them. Keeps routes
tight (``raise ThreadNotFoundError(thread_id)`` vs filling JSON manually).
"""

from __future__ import annotations

from typing import Any


class HaraAPIError(Exception):
    """Base for typed API errors. Each subclass maps to a status code + code."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AuthError(HaraAPIError):
    status_code = 401
    code = "INVALID_TOKEN"


class ThreadNotFoundError(HaraAPIError):
    status_code = 404
    code = "THREAD_NOT_FOUND"


class TurnNotFoundError(HaraAPIError):
    status_code = 404
    code = "TURN_NOT_FOUND"


class TurnNotReadyError(HaraAPIError):
    status_code = 425
    code = "TURN_NOT_READY"


class LLMProviderError(HaraAPIError):
    status_code = 502
    code = "LLM_PROVIDER_ERROR"


class ConnectorError(HaraAPIError):
    status_code = 502
    code = "CONNECTOR_ERROR"


class ConfigError(HaraAPIError):
    status_code = 500
    code = "INVALID_CONFIG"


class SQLValidationError(HaraAPIError):
    status_code = 502
    code = "SQL_VALIDATION_ERROR"


class MessageTooLongError(HaraAPIError):
    """Raised when ``message`` exceeds the configured
    ``[api].max_message_length``. The Pydantic field still enforces a
    defensive 100_000-char hard cap; this typed error is for the
    operator-configured (smaller) limit."""

    status_code = 422
    code = "VALIDATION_ERROR"
