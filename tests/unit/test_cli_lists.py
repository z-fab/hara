"""Tests for `hara connectors list` and `hara providers list`."""

from __future__ import annotations

from typer.testing import CliRunner

from hara.cli.app import app

runner = CliRunner()


def test_connectors_list_includes_builtins() -> None:
    result = runner.invoke(app, ["connectors", "list"])
    assert result.exit_code == 0
    assert "memory" in result.stdout
    assert "sqlite" in result.stdout
    assert "postgres" in result.stdout
    assert "chromadb" in result.stdout


def test_providers_list_includes_builtins() -> None:
    result = runner.invoke(app, ["providers", "list"])
    assert result.exit_code == 0
    assert "openai" in result.stdout
    assert "anthropic" in result.stdout
    assert "google" in result.stdout
    assert "openrouter" in result.stdout
    assert "ollama" in result.stdout
