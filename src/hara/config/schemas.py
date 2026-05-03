"""Discriminated unions for connector configuration.

Connectors are configured under [connectors.sql] / [connectors.vector] / [connectors.session_store]
in hara.toml. The `type` field discriminates which schema applies. Adding a new
connector type means adding a new model + extending the union here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, SecretStr, TypeAdapter

# ---------- SQL connector configs ----------


class SQLiteSQLConfig(BaseModel):
    type: Literal["sqlite"]
    path: Path


class PostgresSQLConfig(BaseModel):
    type: Literal["postgres"]
    url: SecretStr


class MemorySQLConfig(BaseModel):
    type: Literal["memory"]


SQLConnectorConfigUnion: TypeAlias = Annotated[
    SQLiteSQLConfig | PostgresSQLConfig | MemorySQLConfig,
    Field(discriminator="type"),
]


_sql_adapter: TypeAdapter[SQLConnectorConfigUnion] = TypeAdapter(SQLConnectorConfigUnion)


def parse_sql_config(data: dict[str, Any]) -> SQLConnectorConfigUnion:
    """Parse a [connectors.sql] dict into the typed union."""
    return _sql_adapter.validate_python(data)


# ---------- Vector connector configs ----------


class ChromaDBVectorConfig(BaseModel):
    type: Literal["chromadb"]
    persist_directory: Path


class MemoryVectorConfig(BaseModel):
    type: Literal["memory"]


VectorConnectorConfigUnion: TypeAlias = Annotated[
    ChromaDBVectorConfig | MemoryVectorConfig,
    Field(discriminator="type"),
]


_vector_adapter: TypeAdapter[VectorConnectorConfigUnion] = TypeAdapter(VectorConnectorConfigUnion)


def parse_vector_config(data: dict[str, Any]) -> VectorConnectorConfigUnion:
    """Parse a [connectors.vector] dict into the typed union."""
    return _vector_adapter.validate_python(data)


# ---------- Session Store config ----------


class SessionStoreInheritConfig(BaseModel):
    """Default behavior: inherit [connectors.sql] connection but use prefixed tables.

    No `type` field set means inherit. Only `ttl_days` can be customized.
    """

    type: None = None
    ttl_days: int = 7


class SessionStoreSQLiteConfig(BaseModel):
    type: Literal["sqlite"]
    path: Path
    ttl_days: int = 7


class SessionStorePostgresConfig(BaseModel):
    type: Literal["postgres"]
    url: SecretStr
    ttl_days: int = 7


class SessionStoreMemoryConfig(BaseModel):
    type: Literal["memory"]
    ttl_days: int = 7


SessionStoreConfigUnion: TypeAlias = (
    SessionStoreInheritConfig
    | SessionStoreSQLiteConfig
    | SessionStorePostgresConfig
    | SessionStoreMemoryConfig
)
