"""Tests for `hara version`."""

from __future__ import annotations

from typer.testing import CliRunner

from hara import __version__
from hara.cli.app import app

runner = CliRunner()


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout
