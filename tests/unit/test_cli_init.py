"""Tests for the `hara init` wizard."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from hara.cli.commands.init import register
from hara.config.settings import Settings


@pytest.fixture
def app() -> typer.Typer:
    a = typer.Typer()
    register(a)
    return a


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_non_interactive_writes_files(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    env = tmp_path / ".env"
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive"],
    )
    assert result.exit_code == 0
    assert cfg.exists()
    assert env.exists()


def test_non_interactive_writes_valid_toml(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    env = tmp_path / ".env"
    runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive"],
    )
    data = tomllib.loads(cfg.read_text())
    # Pydantic settings validation
    Settings.model_validate(data)


def test_non_interactive_generates_auth_token(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    env = tmp_path / ".env"
    runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive"],
    )
    data = tomllib.loads(cfg.read_text())
    assert len(data["auth"]["token"]) >= 16  # token_urlsafe(32) yields ~43 chars


def test_non_interactive_writes_hara_api_token_to_env(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    env = tmp_path / ".env"
    runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive"],
    )
    env_content = env.read_text()
    assert "HARA_API_TOKEN=" in env_content


def test_existing_config_aborts_without_force(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    cfg.write_text("# existing\n")
    env = tmp_path / ".env"
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive"],
    )
    # Without --force, should refuse
    assert result.exit_code == 1


def test_force_overwrites_existing(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    cfg = tmp_path / "hara.toml"
    cfg.write_text("# existing\n")
    env = tmp_path / ".env"
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env), "--non-interactive", "--force"],
    )
    assert result.exit_code == 0
    assert "[server]" in cfg.read_text()


def test_interactive_writes_files(
    app: typer.Typer,
    runner: CliRunner,
    tmp_path: Path,
) -> None:
    """Drive through the prompts via stdin."""
    cfg = tmp_path / "hara.toml"
    env = tmp_path / ".env"
    # Answers in order:
    # 1. provider (default openai)
    # 2. api key (empty)
    # 3. model hard (default gpt-5)
    # 4. model soft (default gpt-5-mini)
    # 5. embed provider (default openai)
    # 6. embed model (default text-embedding-3-small)
    # 7. sql connector (default sqlite)
    # 8. sql path (default ./data/hara.db)
    # 9. vector connector (default chromadb)
    # 10. persist dir (default ./data/chroma)
    # 11. generate auth token? (default y)
    # 12. agent style (default)
    answers = "\n".join(["", "", "", "", "", "", "", "", "", "", "y", ""]) + "\n"
    result = runner.invoke(
        app,
        ["--config", str(cfg), "--env", str(env)],
        input=answers,
    )
    assert result.exit_code == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert cfg.exists()
    assert env.exists()
    # Validate TOML
    data = tomllib.loads(cfg.read_text())
    Settings.model_validate(data)
