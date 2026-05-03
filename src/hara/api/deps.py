"""FastAPI dependencies (auth + settings/orchestrator getters).

Spec §6 "Autenticação": Bearer token from ``[auth].token``. Validation is
constant-time to avoid timing attacks (``hmac.compare_digest``).

The error response shape follows :mod:`hara.api.errors` — caller doesn't
need to know which code/message; ``require_auth`` raises
:class:`AuthError` and the global handler turns it into the JSON body.
"""

from __future__ import annotations

import hmac

from fastapi import Header, Request

from hara.api.errors import AuthError
from hara.config.settings import Settings


async def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    """Validate ``Authorization: Bearer <token>``.

    ``authorization`` arrives raw; we parse the scheme + value here rather
    than via OAuth2 helpers to match spec §6 wording exactly. Equality
    comparison uses ``hmac.compare_digest`` to avoid timing leaks.
    """
    if authorization is None:
        raise AuthError("missing Authorization header")
    if not authorization.lower().startswith("bearer "):
        raise AuthError("expected Bearer scheme")
    presented = authorization[len("bearer ") :].strip()
    expected = request.app.state.settings.auth.token
    if not expected:
        # Fail-loud: the app should have refused to start with empty token.
        # Defensive — log + 401 instead of crashing the request.
        raise AuthError("server has no token configured")
    if not hmac.compare_digest(presented, expected):
        raise AuthError("invalid token")


def get_settings(request: Request) -> Settings:
    """Pull the validated Settings off ``app.state``.

    Routes that need config (orchestrator factory, message-length cap)
    inject this dep instead of touching ``request.app.state.settings``
    directly — keeps types tight (``Settings``, not ``Any``).
    """
    settings: Settings = request.app.state.settings
    return settings
