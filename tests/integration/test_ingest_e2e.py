"""End-to-end smoke test of the ingest CLI.

Uses file-backed SQLite for SQL so we can re-instantiate the connector
after the CLI exits and run real SELECTs. Vector stays in-memory because
the v0.1 memory connector doesn't persist — vector data is validated
through SessionStore metadata only. `regenerate_on_ingest=false` keeps
this test free of LLM dependencies (semantic-map generation has its own
tests that mock the LLM).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hara.cli.app import app
from hara.config.schemas import SQLiteSQLConfig
from hara.connectors.sql.sqlite import SQLiteConnector
from hara.connectors.testing import FakeEmbedder
from hara.services.session_store import SessionStore


@pytest.mark.integration
def test_ingest_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # OpenAI provider validates the key at construction time. We never reach
    # the live API because we swap the provider's from_config for a fake.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(
        "hara.providers.embeddings.openai.OpenAIEmbeddingsProvider.from_config",
        classmethod(lambda cls, model, settings: FakeEmbedder()),
    )
    work = tmp_path / "proj"
    work.mkdir()
    (work / "raw").mkdir()
    (work / "raw" / "dados.csv").write_text("a,b\n1,2\n3,4\n")
    (work / "raw" / "manual.md").write_text("# Title\n\ncorpo do manual.\n")

    sql_db = work / "hara.db"
    session_db = work / "session.db"

    cfg = work / "hara.toml"
    cfg.write_text(f"""
[models]
hard = {{ provider = "openai", model = "gpt-4" }}
soft = {{ provider = "openai", model = "gpt-4-mini" }}

[embeddings]
provider = "openai"
model = "text-embedding-3-small"

[connectors.sql]
type = "sqlite"
path = "{sql_db}"

[connectors.vector]
type = "memory"

[connectors.session_store]
type = "sqlite"
path = "{session_db}"

[semantic_map]
regenerate_on_ingest = false
""")
    runner = CliRunner()
    r = runner.invoke(
        app,
        ["ingest", "--config", str(cfg), "--from", str(work / "raw")],
    )
    assert r.exit_code == 0, r.stdout

    async def check() -> None:
        # 1. SessionStore metadata: both files registered.
        store = SessionStore(dsn=f"sqlite:///{session_db}")
        sql_rows = await store.list_ingested_files(target="sql")
        vec_rows = await store.list_ingested_files(target="vector")
        assert any(row.relative_path == "dados.csv" for row in sql_rows)
        assert any(row.relative_path == "manual.md" for row in vec_rows)
        vec_row = next(row for row in vec_rows if row.relative_path == "manual.md")
        assert vec_row.chunks_count is not None
        assert vec_row.chunks_count > 0

        # 2. Real SQL data: re-open the SQLite connector and SELECT.
        sql = SQLiteConnector.from_config(SQLiteSQLConfig(type="sqlite", path=sql_db))
        result = await sql.execute_query("SELECT a, b FROM dados ORDER BY a")
        assert result.columns == ["a", "b"]
        assert result.rows == [(1, 2), (3, 4)]

    asyncio.run(check())
