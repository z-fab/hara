"""Tests for `hara doctor` — focused on the Plano 3 agent extension.

Older doctor checks (config/connectors/providers) are exercised indirectly
via integration smoke; here we cover the new sections only.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hara.cli.app import app

runner = CliRunner()


def _write_min_toml(path: Path) -> None:
    """Minimal TOML that lets load_settings succeed (memory connectors,
    no providers needed beyond what's configured)."""
    path.write_text(
        """
[models]
hard = { provider = "openai", model = "gpt-5" }
soft = { provider = "openai", model = "gpt-5-mini" }

[embeddings]
provider = "openai"
model = "text-embedding-3-small"

[connectors.sql]
type = "memory"

[connectors.vector]
type = "memory"
""".strip()
    )


def test_doctor_lists_per_node_model_resolution(tmp_path, monkeypatch) -> None:
    """Node resolution rows should show the resolved provider:model for
    each of planner / sql / synthesizer / verifier (using defaults from
    [models].hard or [models].soft)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    cfg = tmp_path / "hara.toml"
    _write_min_toml(cfg)
    # doctor checks ./data/* — point CWD elsewhere so the missing files trigger WARN
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    # Exit code may be 0 or 1 depending on connector status; we just need
    # the agent-extension rows to be present in stdout.
    assert "node:planner" in result.stdout
    assert "node:sql" in result.stdout
    assert "node:synthesizer" in result.stdout
    assert "node:verifier" in result.stdout
    # planner/verifier default to hard (gpt-5); sql/synthesizer default to soft (gpt-5-mini)
    assert "gpt-5" in result.stdout
    assert "gpt-5-mini" in result.stdout


def test_doctor_warns_when_semantic_maps_missing(tmp_path, monkeypatch) -> None:
    """Missing structured.yaml / unstructured.yaml → WARN rows, not failure."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    cfg = tmp_path / "hara.toml"
    _write_min_toml(cfg)
    monkeypatch.chdir(tmp_path)  # ./data/ doesn't exist here

    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert "structured.yaml" in result.stdout
    assert "unstructured.yaml" in result.stdout
    # WARN keyword shows up in the table
    assert "WARN" in result.stdout or "ausente" in result.stdout


def test_doctor_finds_yamls_via_paths_data_dir(tmp_path, monkeypatch) -> None:
    """When [paths].data_dir points at a folder containing the YAMLs,
    doctor should report them as OK rather than WARN."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake")
    yaml_dir = tmp_path / "ymls"
    yaml_dir.mkdir()
    (yaml_dir / "structured.yaml").write_text("tables: []\n")
    (yaml_dir / "unstructured.yaml").write_text("documents: []\n")

    cfg = tmp_path / "hara.toml"
    cfg.write_text(
        f"""
[models]
hard = {{ provider = "openai", model = "gpt-5" }}
soft = {{ provider = "openai", model = "gpt-5-mini" }}

[embeddings]
provider = "openai"
model = "text-embedding-3-small"

[connectors.sql]
type = "memory"

[connectors.vector]
type = "memory"

[paths]
data_dir = "{yaml_dir}"
""".strip()
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--config", str(cfg)])
    assert "structured.yaml" in result.stdout
    assert "unstructured.yaml" in result.stdout
    # Both YAML files exist — neither row should carry the
    # "ausente" hint (which is the WARN branch).
    assert "ausente" not in result.stdout
