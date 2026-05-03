"""Tests for connector config discriminated unions."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from hara.config.schemas import (
    ChromaDBVectorConfig,
    MemorySQLConfig,
    MemoryVectorConfig,
    PostgresSQLConfig,
    SQLConnectorConfigUnion,
    SQLiteSQLConfig,
    VectorConnectorConfigUnion,
    parse_sql_config,
    parse_vector_config,
)

# Public-API smoke check: the union aliases must be importable for downstream
# typing even if no individual test references them directly.
__all__ = ["SQLConnectorConfigUnion", "VectorConnectorConfigUnion"]


def test_sqlite_config_requires_path() -> None:
    cfg = parse_sql_config({"type": "sqlite", "path": "./data/hara.db"})
    assert isinstance(cfg, SQLiteSQLConfig)
    assert cfg.path == Path("./data/hara.db")


def test_sqlite_config_missing_path_raises() -> None:
    with pytest.raises(ValidationError):
        parse_sql_config({"type": "sqlite"})


def test_postgres_config_requires_url() -> None:
    cfg = parse_sql_config({"type": "postgres", "url": "postgresql://u:p@h/db"})
    assert isinstance(cfg, PostgresSQLConfig)
    assert cfg.url.get_secret_value() == "postgresql://u:p@h/db"


def test_memory_sql_config() -> None:
    cfg = parse_sql_config({"type": "memory"})
    assert isinstance(cfg, MemorySQLConfig)


def test_unknown_sql_type_raises() -> None:
    with pytest.raises(ValidationError):
        parse_sql_config({"type": "mysql"})


def test_chromadb_vector_config() -> None:
    cfg = parse_vector_config({"type": "chromadb", "persist_directory": "./data/chroma"})
    assert isinstance(cfg, ChromaDBVectorConfig)
    assert cfg.persist_directory == Path("./data/chroma")


def test_memory_vector_config() -> None:
    cfg = parse_vector_config({"type": "memory"})
    assert isinstance(cfg, MemoryVectorConfig)


def test_unknown_vector_type_raises() -> None:
    with pytest.raises(ValidationError):
        parse_vector_config({"type": "qdrant"})  # not in v0.1
