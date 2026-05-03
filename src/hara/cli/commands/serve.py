"""`hara serve` — boot the FastAPI app via uvicorn.

Spec §6 forces ``--workers=1`` because turns are tracked as
``asyncio.Task`` in process-local memory; multi-worker would lose that
visibility. v0.2 adds heartbeat-based reaper to allow more workers.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("serve")
    def serve_cmd(  # pyright: ignore[reportUnusedFunction]
        host: str = typer.Option("0.0.0.0", "--host"),  # noqa: S104
        port: int = typer.Option(8000, "--port"),
        workers: int = typer.Option(1, "--workers"),
        config: Path = typer.Option(  # noqa: B008
            Path("hara.toml"),
            "--config",
            "-c",
            help="Path to hara.toml.",
        ),
    ) -> None:
        """Boot the HARA HTTP API. Forces --workers=1 (spec §6)."""
        if workers != 1:
            console.print(
                f"[yellow]warning: --workers={workers} requested; "
                "v0.1 forces --workers=1 (spec §6). overriding.[/]"
            )
            workers = 1

        try:
            import uvicorn  # noqa: PLC0415
        except ImportError as e:
            raise typer.BadParameter(
                "the api extra is not installed; run `pip install hara[api]`"
            ) from e

        from hara.api.app import create_app  # noqa: PLC0415
        from hara.config.settings import load_settings  # noqa: PLC0415

        settings = load_settings(toml_file=config)
        if not settings.auth.token:
            console.print(
                "[red]error: [auth].token is empty; refusing to serve unauthenticated.[/]"
            )
            raise typer.Exit(code=1)

        app_instance = create_app(settings)
        uvicorn.run(app_instance, host=host, port=port, workers=workers)
