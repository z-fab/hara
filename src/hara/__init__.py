"""HARA — Hybrid Agent for Retrieval and Answering."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Read from installed package metadata so __version__ tracks pyproject.toml
    # automatically (no manual sync). Codex review #1 P2: prior literal got stale.
    __version__ = _pkg_version("hara")
except PackageNotFoundError:
    # Fallback when running from a checkout without the package installed
    # (e.g., `python -c "from hara import ..."` outside `pip install -e .`).
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
