"""Tests for the `hara ingest` CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hara.cli.app import app
from hara.ingest.pipelines.structured import (
    StructuredItem,
    StructuredItemStatus,
    StructuredResult,
)
from hara.ingest.pipelines.unstructured import UnstructuredResult
from hara.ingest.service import IngestReport


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _min_toml(tmp_path: Path) -> str:
    return f"""
[models]
hard = {{ provider = "openai", model = "gpt-4" }}
soft = {{ provider = "openai", model = "gpt-4-mini" }}

[embeddings]
provider = "openai"
model = "text-embedding-3-small"

[connectors.sql]
type = "memory"

[connectors.vector]
type = "memory"

[connectors.session_store]
type = "sqlite"
path = "{tmp_path / "session.db"}"

[semantic_map]
regenerate_on_ingest = false
"""


def test_ingest_command_exists(runner: CliRunner) -> None:
    r = runner.invoke(app, ["ingest", "--help"])
    assert r.exit_code == 0
    assert "--from" in r.stdout
    assert "--config" in r.stdout
    assert "--target" in r.stdout


def test_ingest_invokes_service(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_path / "x.csv").write_text("a\n1\n")
    cfg = tmp_path / "hara.toml"
    cfg.write_text(_min_toml(tmp_path))

    captured: dict[str, object] = {}

    with patch("hara.cli.commands.ingest.run_ingest") as mock_run:

        async def fake(**kwargs: object) -> object:
            captured.update(kwargs)
            return IngestReport(
                scanned=1,
                ignored=0,
                structured=StructuredResult(items=[]),
                unstructured=UnstructuredResult(items=[]),
            )

        mock_run.side_effect = fake
        r = runner.invoke(
            app,
            [
                "ingest",
                "--config",
                str(cfg),
                "--from",
                str(tmp_path),
                "--target",
                "sql",
                "--strict",
            ],
        )
    assert r.exit_code == 1, r.stdout  # no OK items in fake report -> exit 1
    assert mock_run.called
    assert captured["target"] == "sql"
    assert captured["strict"] is True


def test_ingest_triggers_semantic_map_when_enabled_and_has_ok(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_path / "x.csv").write_text("a\n1\n")
    cfg = tmp_path / "hara.toml"
    # Override default regenerate_on_ingest=false from _min_toml fixture
    cfg.write_text(
        _min_toml(tmp_path).replace("regenerate_on_ingest = false", "regenerate_on_ingest = true")
    )

    with (
        patch("hara.cli.commands.ingest.run_ingest") as mock_run,
        patch("hara.cli.commands.ingest._run_semantic_map") as mock_sm,
    ):

        async def fake(**kwargs: object) -> object:
            return IngestReport(
                scanned=1,
                ignored=0,
                structured=StructuredResult(
                    items=[
                        StructuredItem(
                            relative_path="x.csv",
                            table_name="x",
                            status=StructuredItemStatus.OK,
                            rows=1,
                        )
                    ]
                ),
                unstructured=UnstructuredResult(items=[]),
            )

        mock_run.side_effect = fake

        async def fake_sm(*args: object, **kwargs: object) -> None:
            return None

        mock_sm.side_effect = fake_sm

        r = runner.invoke(
            app,
            ["ingest", "--config", str(cfg), "--from", str(tmp_path)],
        )
    assert r.exit_code == 0, r.stdout
    assert mock_sm.called


def test_ingest_skips_semantic_map_when_no_ok_items(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-trigger must not waste LLM calls when every file was skipped/failed."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    (tmp_path / "x.csv").write_text("a\n1\n")
    cfg = tmp_path / "hara.toml"
    cfg.write_text(
        _min_toml(tmp_path).replace("regenerate_on_ingest = false", "regenerate_on_ingest = true")
    )

    with (
        patch("hara.cli.commands.ingest.run_ingest") as mock_run,
        patch("hara.cli.commands.ingest._run_semantic_map") as mock_sm,
    ):

        async def fake(**kwargs: object) -> object:
            # No OK items → no auto-trigger
            return IngestReport(
                scanned=1,
                ignored=0,
                structured=StructuredResult(items=[]),
                unstructured=UnstructuredResult(items=[]),
            )

        mock_run.side_effect = fake
        mock_sm.return_value = None

        runner.invoke(
            app,
            ["ingest", "--config", str(cfg), "--from", str(tmp_path)],
        )
    # No OK items also means exit code 1 (existing behavior); but most importantly:
    assert not mock_sm.called
