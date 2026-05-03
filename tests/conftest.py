"""Shared pytest fixtures for HARA tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip HARA_* env vars to prevent test contamination."""
    for var in list(os.environ):
        if var.startswith("HARA_") or var.endswith("_API_KEY"):
            monkeypatch.delenv(var, raising=False)
    # Disable Rich/Click colored output so CLI assertions can match raw substrings
    # (Rich splits option names across ANSI escape codes when color is on).
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
