"""Smoke test: hara.toml.example must parse + validate against Settings.

This guards against drift — every time we add a new Settings field, the
template should be updated to match. The test is minimal but pinning.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from hara.config.settings import Settings


def test_hara_toml_example_parses_and_validates() -> None:
    repo_root = Path(__file__).parent.parent.parent
    raw = (repo_root / "hara.toml.example").read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    Settings.model_validate(data)
