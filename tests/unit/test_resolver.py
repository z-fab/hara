"""Tests for per-node model resolution."""

from __future__ import annotations

from typing import cast

import pytest

from hara.config.settings import ModelRef, ModelsSettings
from hara.providers.resolver import resolve_node_model_ref


def _settings(**overrides: ModelRef | None) -> ModelsSettings:
    base = {
        "hard": ModelRef(provider="openai", model="gpt-5"),
        "soft": ModelRef(provider="openai", model="gpt-5-mini"),
    }
    base.update({k: v for k, v in overrides.items() if v is not None})
    return ModelsSettings(**cast(dict, base))


def test_planner_defaults_to_hard() -> None:
    s = _settings()
    ref = resolve_node_model_ref("planner", s)
    assert ref.model == "gpt-5"


def test_verifier_defaults_to_hard() -> None:
    s = _settings()
    ref = resolve_node_model_ref("verifier", s)
    assert ref.model == "gpt-5"


def test_sql_defaults_to_soft() -> None:
    s = _settings()
    ref = resolve_node_model_ref("sql", s)
    assert ref.model == "gpt-5-mini"


def test_synthesizer_defaults_to_soft() -> None:
    s = _settings()
    ref = resolve_node_model_ref("synthesizer", s)
    assert ref.model == "gpt-5-mini"


def test_per_node_override_wins() -> None:
    s = _settings(sql=ModelRef(provider="openai", model="gpt-5-nano"))
    ref = resolve_node_model_ref("sql", s)
    assert ref.model == "gpt-5-nano"


def test_unknown_node_raises() -> None:
    s = _settings()
    with pytest.raises(ValueError, match="Unknown node"):
        resolve_node_model_ref("nonexistent", s)
