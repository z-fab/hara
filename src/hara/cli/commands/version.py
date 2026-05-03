"""`hara version` — prints the package version."""

from __future__ import annotations

import typer

from hara import __version__


def version_command() -> None:
    """Print HARA version."""
    typer.echo(f"HARA {__version__}")
