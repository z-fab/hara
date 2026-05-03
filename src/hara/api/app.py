"""FastAPI app factory.

Builds the FastAPI instance and attaches the validated Settings to
``app.state``. Routes are wired in subsequent tasks; this skeleton just
returns an empty FastAPI ready to extend.
"""

from __future__ import annotations

from fastapi import FastAPI

from hara import __version__
from hara.api.lifecycle import lifespan
from hara.config.settings import Settings


def create_app(settings: Settings) -> FastAPI:
    """Build the FastAPI app from a validated :class:`Settings`.

    Caller (``hara serve``) is responsible for instantiating ``Settings``
    via :func:`hara.config.settings.load_settings` and passing it in.
    Tests inject a minimal in-memory Settings.
    """
    docs_url = "/docs" if settings.api.open_docs else None
    redoc_url = "/redoc" if settings.api.open_docs else None
    # When locked, we set openapi_url=None so FastAPI does NOT auto-register
    # an un-authenticated /openapi.json route; _gate_openapi_routes installs
    # an auth-protected equivalent below.
    openapi_url = "/openapi.json" if settings.api.open_docs else None

    app = FastAPI(
        title="HARA",
        version=__version__,
        description=(
            "Hybrid Agent for Retrieval and Answering — multi-agent for "
            "hybrid SQL+document Q&A. See https://github.com/.../hara"
        ),
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    app.state.settings = settings
    _register_error_handlers(app)
    if not settings.api.open_docs:
        _gate_openapi_routes(app)

    if settings.api.cors.allowed_origins:
        from fastapi.middleware.cors import CORSMiddleware  # noqa: PLC0415

        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.api.cors.allowed_origins),
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    from hara.api.routes import ops as ops_routes  # noqa: PLC0415

    app.include_router(ops_routes.router)
    from hara.api.routes import threads as threads_routes  # noqa: PLC0415

    app.include_router(threads_routes.router)
    from hara.api.routes import turns as turns_routes  # noqa: PLC0415

    app.include_router(turns_routes.router)
    from hara.api.routes import convenience as convenience_routes  # noqa: PLC0415

    app.include_router(convenience_routes.router)
    return app


def _register_error_handlers(app: FastAPI) -> None:
    """Map :class:`HaraAPIError` subclasses to consistent JSON responses."""
    from fastapi import Request  # noqa: PLC0415
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    from hara.api.errors import HaraAPIError  # noqa: PLC0415

    @app.exception_handler(HaraAPIError)
    async def _hara_error_handler(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: HaraAPIError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )


def _gate_openapi_routes(app: FastAPI) -> None:
    """Wrap /openapi.json + /docs + /redoc with the Bearer auth dependency.

    FastAPI registers these as un-authenticated by default; when
    ``[api].open_docs=false`` we replace them with auth-protected
    equivalents so private deployments can keep the docs gated.
    """
    from typing import Any  # noqa: PLC0415

    from fastapi import Depends  # noqa: PLC0415
    from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html  # noqa: PLC0415
    from fastapi.openapi.utils import get_openapi  # noqa: PLC0415

    from hara.api.deps import require_auth  # noqa: PLC0415

    @app.get("/openapi.json", dependencies=[Depends(require_auth)], include_in_schema=False)
    async def _openapi() -> dict[str, Any]:  # pyright: ignore[reportUnusedFunction]
        return get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

    @app.get("/docs", dependencies=[Depends(require_auth)], include_in_schema=False)
    async def _swagger() -> Any:  # pyright: ignore[reportUnusedFunction]
        return get_swagger_ui_html(openapi_url="/openapi.json", title=app.title)

    @app.get("/redoc", dependencies=[Depends(require_auth)], include_in_schema=False)
    async def _redoc() -> Any:  # pyright: ignore[reportUnusedFunction]
        return get_redoc_html(openapi_url="/openapi.json", title=app.title)
