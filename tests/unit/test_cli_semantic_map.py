"""Tests for the `hara semantic-map` CLI."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from hara.cli.app import app
from hara.services.semantic_map import MapBuildResult


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
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_semantic_map_command_exists(runner: CliRunner) -> None:
    r = runner.invoke(app, ["semantic-map", "--help"])
    assert r.exit_code == 0
    assert "--config" in r.stdout
    assert "--force" in r.stdout


def test_semantic_map_force_runs_builders(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = tmp_path / "hara.toml"
    cfg.write_text(_min_toml(tmp_path))
    out_dir = tmp_path / "data"

    with (
        patch("hara.cli.commands.semantic_map.build_structured_map") as ms,
        patch("hara.cli.commands.semantic_map.build_unstructured_map") as mu,
        # The CLI builds an LLM via real provider; bypass that to avoid
        # needing API keys in unit tests.
        patch("hara.cli.commands.semantic_map._build_llm") as ml,
    ):

        async def fake_s(*args: object, **kwargs: object) -> MapBuildResult:
            return MapBuildResult(data={"tables": []}, failed_ids=[])

        async def fake_u(*args: object, **kwargs: object) -> MapBuildResult:
            return MapBuildResult(data={"documents": []}, failed_ids=[])

        ms.side_effect = fake_s
        mu.side_effect = fake_u
        ml.return_value = object()  # builders are mocked, llm is opaque
        r = runner.invoke(
            app,
            [
                "semantic-map",
                "--config",
                str(cfg),
                "--force",
                "--data-dir",
                str(out_dir),
            ],
        )
    assert r.exit_code == 0, r.stdout
    assert (out_dir / "structured.yaml").exists()
    assert (out_dir / "unstructured.yaml").exists()
    # Clean run -> state-lock is saved.
    assert (out_dir / ".semantic_map_state.json").exists()
    assert ms.called
    assert mu.called


def test_semantic_map_does_not_save_state_when_builder_reports_failures(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: if any table/doc ended up in ``failed_ids`` the CLI must
    NOT lock the snapshot — otherwise the next non-``--force`` run would
    silently skip the items that were dropped from the YAML.

    Also: with the transactional-publish fix, the live YAML files must NOT
    be overwritten on partial failure — but a ``.partial.yaml`` debug file
    is acceptable.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = tmp_path / "hara.toml"
    cfg.write_text(_min_toml(tmp_path))
    out_dir = tmp_path / "data"

    with (
        patch("hara.cli.commands.semantic_map.build_structured_map") as ms,
        patch("hara.cli.commands.semantic_map.build_unstructured_map") as mu,
        patch("hara.cli.commands.semantic_map._build_llm") as ml,
    ):

        async def fake_s(*args: object, **kwargs: object) -> MapBuildResult:
            return MapBuildResult(data={"tables": []}, failed_ids=["broken_table"])

        async def fake_u(*args: object, **kwargs: object) -> MapBuildResult:
            return MapBuildResult(data={"documents": []}, failed_ids=[])

        ms.side_effect = fake_s
        mu.side_effect = fake_u
        ml.return_value = object()
        r = runner.invoke(
            app,
            [
                "semantic-map",
                "--config",
                str(cfg),
                "--force",
                "--data-dir",
                str(out_dir),
            ],
        )
    assert r.exit_code == 0, r.stdout
    assert "broken_table" in r.stdout
    assert "not saving state-lock" in r.stdout
    # State-lock must be absent so the next run retries.
    assert not (out_dir / ".semantic_map_state.json").exists()
    # Live YAMLs must NOT be written on partial failure (transactional publish).
    assert not (out_dir / "structured.yaml").exists()
    assert not (out_dir / "unstructured.yaml").exists()


def test_semantic_map_does_not_overwrite_existing_yaml_on_partial_failure(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing good YAMLs must survive a partial-failure run.

    Transactional publish: on any builder failed_id, the live ``.yaml``
    files are left untouched. Partial output may be written to
    ``.partial.yaml`` for debugging.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = tmp_path / "hara.toml"
    cfg.write_text(_min_toml(tmp_path))
    out_dir = tmp_path / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pre-existing known-good content.
    structured_known = "tables:\n- table_name: previous_good\n  description: keep me\n"
    unstructured_known = "documents:\n- file_id: prev_doc\n  title: keep me too\n"
    (out_dir / "structured.yaml").write_text(structured_known)
    (out_dir / "unstructured.yaml").write_text(unstructured_known)

    with (
        patch("hara.cli.commands.semantic_map.build_structured_map") as ms,
        patch("hara.cli.commands.semantic_map.build_unstructured_map") as mu,
        patch("hara.cli.commands.semantic_map._build_llm") as ml,
    ):

        async def fake_s(*args: object, **kwargs: object) -> MapBuildResult:
            # Partial output with a failed_id: must NOT replace the live YAML.
            return MapBuildResult(
                data={"tables": [{"table_name": "partial", "description": "x"}]},
                failed_ids=["broken_table"],
            )

        async def fake_u(*args: object, **kwargs: object) -> MapBuildResult:
            return MapBuildResult(data={"documents": []}, failed_ids=[])

        ms.side_effect = fake_s
        mu.side_effect = fake_u
        ml.return_value = object()
        r = runner.invoke(
            app,
            [
                "semantic-map",
                "--config",
                str(cfg),
                "--force",
                "--data-dir",
                str(out_dir),
            ],
        )
    assert r.exit_code == 0, r.stdout
    # Live YAMLs preserved.
    assert (out_dir / "structured.yaml").read_text() == structured_known
    assert (out_dir / "unstructured.yaml").read_text() == unstructured_known
    # State-lock not saved.
    assert not (out_dir / ".semantic_map_state.json").exists()
