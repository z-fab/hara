"""Allow `python -m hara` to invoke the CLI."""

from __future__ import annotations

from hara.cli.app import app

if __name__ == "__main__":
    app()
