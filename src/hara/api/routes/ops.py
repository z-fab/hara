"""Operations endpoints — /health, /version, /doctor.

/health is the only un-authenticated route in the API (spec §6).
/version returns the installed package version.
/doctor mirrors `hara doctor` output as JSON — 200 if all checks pass,
503 (status="degraded") if any check fails.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from hara import __version__
from hara.api.deps import require_auth

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. No auth, no DB hit."""
    return {"status": "ok"}


@router.get("/version", dependencies=[Depends(require_auth)])
async def version() -> dict[str, str]:
    return {"version": __version__}


@router.get("/doctor", dependencies=[Depends(require_auth)])
async def doctor(request: Request) -> Any:
    """Subset of `hara doctor` — returns 200 with checks list, or 503 if any failed."""
    from hara.cli.commands.doctor import run_checks  # noqa: PLC0415

    settings = request.app.state.settings
    results = await run_checks(settings)
    has_fail = any(r.status == "FAIL" for r in results)
    payload: dict[str, Any] = {
        "status": "ok" if not has_fail else "degraded",
        "checks": [{"check": r.check, "status": r.status, "detail": r.detail} for r in results],
    }
    if has_fail:
        return JSONResponse(status_code=503, content=payload)
    return payload
