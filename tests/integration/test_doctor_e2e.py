"""End-to-end doctor test using a minimal in-memory config."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from hara.cli.app import app

runner = CliRunner()


@pytest.fixture
def memory_only_toml(tmp_path: Path) -> Path:
    """A hara.toml using only in-memory connectors — no external deps."""
    toml_path = tmp_path / "hara.toml"
    toml_path.write_text(
        """
[auth]
token = "test"

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

[connectors.session_store]
ttl_days = 7
""",
        encoding="utf-8",
    )
    return toml_path


def test_doctor_with_memory_config(memory_only_toml: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HARA_API_TOKEN", "test")
    result = runner.invoke(app, ["doctor", "--config", str(memory_only_toml)])
    # Doctor exit code 0 means all checks passed (we use memory + fake key, so no live LLM call).
    assert result.exit_code == 0
    assert "config" in result.stdout.lower() or "ok" in result.stdout.lower()
