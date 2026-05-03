"""Tests for `hara serve`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import typer
from typer.testing import CliRunner

from hara.cli.commands.serve import register


def _app() -> typer.Typer:
    a = typer.Typer()
    register(a)
    return a


def test_serve_forces_workers_to_1(monkeypatch, tmp_path: Path) -> None:
    """Spec §17 critério 8: even with --workers=4, must override to 1."""
    fake_uvicorn = MagicMock()
    monkeypatch.setattr("uvicorn.run", fake_uvicorn.run, raising=False)

    fake_settings = MagicMock()
    fake_settings.auth.token = "secret"
    monkeypatch.setattr(
        "hara.cli.commands.serve.load_settings",
        lambda toml_file: fake_settings,
        raising=False,
    )
    fake_app = MagicMock()
    monkeypatch.setattr(
        "hara.cli.commands.serve.create_app",
        lambda _settings: fake_app,
        raising=False,
    )

    # The settings/create_app patches won't take effect because the imports
    # are inline (lazy) inside serve_cmd. We need to patch the modules they
    # resolve to:
    monkeypatch.setattr("hara.config.settings.load_settings", lambda toml_file: fake_settings)
    monkeypatch.setattr("hara.api.app.create_app", lambda _settings: fake_app)
    # uvicorn.run patched on the uvicorn module:
    import uvicorn  # noqa: PLC0415

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn.run)

    runner = CliRunner()
    cfg = tmp_path / "hara.toml"
    cfg.write_text("# placeholder\n")
    result = runner.invoke(_app(), ["--config", str(cfg), "--workers", "4"])

    assert result.exit_code == 0
    fake_uvicorn.run.assert_called_once()
    kwargs = fake_uvicorn.run.call_args.kwargs
    assert kwargs.get("workers") == 1


def test_serve_rejects_empty_auth_token(monkeypatch, tmp_path: Path) -> None:
    fake_settings = MagicMock()
    fake_settings.auth.token = ""
    monkeypatch.setattr("hara.config.settings.load_settings", lambda toml_file: fake_settings)
    monkeypatch.setattr("hara.api.app.create_app", lambda _settings: MagicMock())
    import uvicorn  # noqa: PLC0415

    monkeypatch.setattr(uvicorn, "run", MagicMock())

    runner = CliRunner()
    cfg = tmp_path / "hara.toml"
    cfg.write_text("# placeholder\n")
    result = runner.invoke(_app(), ["--config", str(cfg)])

    assert result.exit_code == 1
    assert "auth].token is empty" in result.stdout or "refusing to serve" in result.stdout


def test_serve_default_workers_1(monkeypatch, tmp_path: Path) -> None:
    fake_uvicorn = MagicMock()
    fake_settings = MagicMock()
    fake_settings.auth.token = "secret"
    monkeypatch.setattr("hara.config.settings.load_settings", lambda toml_file: fake_settings)
    monkeypatch.setattr("hara.api.app.create_app", lambda _settings: MagicMock())
    import uvicorn  # noqa: PLC0415

    monkeypatch.setattr(uvicorn, "run", fake_uvicorn.run)

    runner = CliRunner()
    cfg = tmp_path / "hara.toml"
    cfg.write_text("# placeholder\n")
    result = runner.invoke(_app(), ["--config", str(cfg)])

    assert result.exit_code == 0
    fake_uvicorn.run.assert_called_once()
    assert fake_uvicorn.run.call_args.kwargs.get("workers") == 1
