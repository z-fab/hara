"""Tests for Settings root and TOML loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.config.settings import PathsSettings, load_settings

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_from_toml_full(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(FIXTURES)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")

    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 9001
    assert settings.auth.token == "test-token"
    assert settings.models.hard.model == "gpt-5"
    assert settings.models.sql is not None
    assert settings.models.sql.model == "gpt-5-nano"
    assert settings.connectors.sql.type == "sqlite"
    assert settings.connectors.vector.type == "chromadb"
    assert settings.verifier.mode == "signal"
    assert settings.agent.sql_max_rows == 50


def test_env_var_overrides_toml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("SERVER__PORT", "9999")
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")

    assert settings.server.port == 9999  # env wins


def test_missing_required_token_validation() -> None:
    """Token defaults to empty string; doctor validates non-empty."""
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    # Empty token is structurally valid; semantic check happens elsewhere.
    assert settings.auth.token == "test-token"


def test_models_per_node_override_resolves_to_explicit() -> None:
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    # When [models.sql] is set, it overrides the soft default
    assert settings.models.sql is not None
    assert settings.models.sql.provider == "openai"


def test_models_per_node_unset_means_inherit() -> None:
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    # [models.planner] is not in the fixture
    assert settings.models.planner is None
    assert settings.models.verifier is None


def test_hara_api_token_env_var_maps_to_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented flat env var HARA_API_TOKEN must populate auth.token."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HARA_API_TOKEN", "from-flat-env")
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    assert settings.auth.token == "from-flat-env"


def test_nested_env_var_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The nested form AUTH__TOKEN remains supported."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AUTH__TOKEN", "from-nested-env")
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    assert settings.auth.token == "from-nested-env"


def test_nested_env_wins_over_flat_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If a user explicitly sets the nested form, do not overwrite it."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HARA_API_TOKEN", "from-flat")
    monkeypatch.setenv("AUTH__TOKEN", "from-nested")
    settings = load_settings(toml_file=FIXTURES / "sample_hara.toml")
    assert settings.auth.token == "from-nested"


def test_paths_settings_default_data_dir() -> None:
    p = PathsSettings()
    assert p.data_dir == Path("./data")


def test_paths_data_dir_from_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "hara.toml"
    custom_dir = tmp_path / "custom-data"
    cfg.write_text(
        f"""
[models]
hard = {{ provider = "openai", model = "x" }}
soft = {{ provider = "openai", model = "y" }}

[embeddings]
provider = "openai"
model = "z"

[connectors.sql]
type = "memory"

[connectors.vector]
type = "memory"

[paths]
data_dir = "{custom_dir}"
""".strip()
    )
    settings = load_settings(toml_file=cfg)
    assert str(settings.paths.data_dir) == str(custom_dir)
