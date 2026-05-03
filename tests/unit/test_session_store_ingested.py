"""Tests for hara_ingested_files operations on SessionStore."""

from __future__ import annotations

from pathlib import Path

import pytest

from hara.config.schemas import (
    MemorySQLConfig,
    MemoryVectorConfig,
    SessionStoreInheritConfig,
    SessionStoreMemoryConfig,
    SessionStoreSQLiteConfig,
    SQLiteSQLConfig,
)
from hara.config.settings import (
    AgentSettings,
    APISettings,
    AuthSettings,
    ConnectorsSettings,
    EmbeddingsSettings,
    IngestSettings,
    LoggingSettings,
    ModelRef,
    ModelsSettings,
    ProvidersSettings,
    SemanticMapSettings,
    ServerSettings,
    Settings,
    VerifierSettings,
)
from hara.services.session_store import (
    IngestedFileRecord,
    SessionStore,
    derive_session_dsn,
)


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(dsn=f"sqlite:///{tmp_path / 'test.db'}")
    await s.initialize()
    return s


async def test_record_ingested_file_inserts(store: SessionStore) -> None:
    rec = IngestedFileRecord(
        content_hash="a" * 64,
        target="sql",
        relative_path="2024/dados.csv",
        table_or_file_id="2024_dados",
        rows_count=100,
    )
    await store.record_ingested_file(rec)

    found = await store.find_ingested_file(content_hash="a" * 64, target="sql")
    assert found is not None
    assert found.table_or_file_id == "2024_dados"
    assert found.rows_count == 100
    assert found.chunks_count is None


async def test_record_ingested_file_is_idempotent_on_pk(
    store: SessionStore,
) -> None:
    rec = IngestedFileRecord(
        content_hash="b" * 64,
        target="vector",
        relative_path="manual.pdf",
        table_or_file_id="manual",
        chunks_count=42,
        tokens_used=12_000,
    )
    await store.record_ingested_file(rec)
    # Second insert with same (content_hash, target) but different chunks_count
    # must REPLACE (upsert), not error or duplicate.
    await store.record_ingested_file(rec.model_copy(update={"chunks_count": 50}))

    found = await store.find_ingested_file(content_hash="b" * 64, target="vector")
    assert found is not None
    assert found.chunks_count == 50

    listed = await store.list_ingested_files(target="vector")
    assert len(listed) == 1


async def test_list_ingested_files_filters_by_target(store: SessionStore) -> None:
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="c" * 64,
            target="sql",
            relative_path="t.csv",
            table_or_file_id="t",
            rows_count=10,
        )
    )
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="d" * 64,
            target="vector",
            relative_path="d.pdf",
            table_or_file_id="d",
            chunks_count=5,
        )
    )
    sql_only = await store.list_ingested_files(target="sql")
    vec_only = await store.list_ingested_files(target="vector")
    assert {r.table_or_file_id for r in sql_only} == {"t"}
    assert {r.table_or_file_id for r in vec_only} == {"d"}


async def test_find_ingested_file_returns_none_when_absent(
    store: SessionStore,
) -> None:
    assert await store.find_ingested_file(content_hash="x" * 64, target="sql") is None


async def test_delete_ingested_file_by_id(store: SessionStore) -> None:
    rec = IngestedFileRecord(
        content_hash="e" * 64,
        target="vector",
        relative_path="old.pdf",
        table_or_file_id="old",
        chunks_count=3,
    )
    await store.record_ingested_file(rec)
    await store.delete_ingested_file(content_hash="e" * 64, target="vector")
    assert await store.find_ingested_file(content_hash="e" * 64, target="vector") is None


async def test_delete_ingested_files_by_target_id_removes_old_hashes(
    store: SessionStore,
) -> None:
    """When `replace` re-ingests a CSV with new content, the old (content_hash, target)
    row would orphan because the PK changes. delete_by_target_id wipes any prior
    record sharing the same logical id so the dedup snapshot stays accurate.
    """
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="f" * 64,
            target="sql",
            relative_path="dados.csv",
            table_or_file_id="dados",
            rows_count=10,
        )
    )
    await store.record_ingested_file(
        IngestedFileRecord(
            content_hash="g" * 64,
            target="vector",
            relative_path="dados.md",
            table_or_file_id="dados",
            chunks_count=2,
        )
    )
    await store.delete_ingested_files_by_target_id(target="sql", table_or_file_id="dados")
    # SQL row gone; vector row with same name still there (different target)
    assert await store.find_ingested_file(content_hash="f" * 64, target="sql") is None
    assert await store.find_ingested_file(content_hash="g" * 64, target="vector") is not None


# ---------- derive_session_dsn ----------


def _make_settings(*, sql_cfg, session_cfg) -> Settings:
    return Settings(
        server=ServerSettings(),
        auth=AuthSettings(),
        api=APISettings(),
        models=ModelsSettings(
            hard=ModelRef(provider="openai", model="x"),
            soft=ModelRef(provider="openai", model="y"),
        ),
        embeddings=EmbeddingsSettings(provider="openai", model="z"),
        providers=ProvidersSettings(),
        connectors=ConnectorsSettings(
            sql=sql_cfg,
            vector=MemoryVectorConfig(type="memory"),
            session_store=session_cfg,
        ),
        verifier=VerifierSettings(),
        agent=AgentSettings(),
        ingest=IngestSettings(),
        semantic_map=SemanticMapSettings(),
        logging=LoggingSettings(),
    )


def test_derive_dsn_explicit_sqlite(tmp_path: Path) -> None:
    s = _make_settings(
        sql_cfg=SQLiteSQLConfig(type="sqlite", path=tmp_path / "data.db"),
        session_cfg=SessionStoreSQLiteConfig(type="sqlite", path=tmp_path / "sess.db"),
    )
    assert derive_session_dsn(s) == f"sqlite:///{tmp_path / 'sess.db'}"


def test_derive_dsn_inherit_from_sqlite(tmp_path: Path) -> None:
    s = _make_settings(
        sql_cfg=SQLiteSQLConfig(type="sqlite", path=tmp_path / "data.db"),
        session_cfg=SessionStoreInheritConfig(),
    )
    assert derive_session_dsn(s) == f"sqlite:///{tmp_path / 'data.db'}"


def test_derive_dsn_memory_session() -> None:
    s = _make_settings(
        sql_cfg=MemorySQLConfig(type="memory"),
        session_cfg=SessionStoreMemoryConfig(type="memory"),
    )
    # 4-slash form: SessionStore strips 'sqlite:///' -> leaves ':memory:'
    # which aiosqlite recognises as the in-memory SQLite database.
    assert derive_session_dsn(s) == "sqlite:///:memory:"


async def test_session_store_accepts_memory_dsn() -> None:
    """The DSN form derive_session_dsn produces is parseable by SessionStore.

    Note: actually using ``:memory:`` for production storage doesn't work
    (each method opens a fresh aiosqlite connection so state isn't shared).
    This test only guards against the prefix-strip regression - that
    ``SessionStore(dsn="sqlite:///:memory:")`` doesn't crash and resolves
    the path to literal ``:memory:`` that ``aiosqlite.connect`` accepts.
    """
    s = SessionStore(dsn="sqlite:///:memory:")
    # Just must not raise; we don't assert state persistence.
    await s.initialize()
