"""Pydantic-Settings root for HARA.

Layers (highest priority first):
    1. Environment variables (with `__` separator for nesting)
    2. .env file (loaded via python-dotenv into os.environ before instantiation)
    3. hara.toml (via TomlConfigSettingsSource)
    4. Defaults declared on each Settings model

Call `load_settings()` once at the entrypoint of CLI/API. The returned Settings
instance is immutable after creation.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from hara.config.schemas import (
    SessionStoreConfigUnion,
    SessionStoreInheritConfig,
    SQLConnectorConfigUnion,
    VectorConnectorConfigUnion,
)

# ---------- Sub-settings models ----------


class ServerSettings(BaseModel):
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8000


class AuthSettings(BaseModel):
    token: str = ""


class CorsSettings(BaseModel):
    allowed_origins: list[str] = Field(default_factory=list)


class APISettings(BaseModel):
    max_message_length: int = 8000
    snippet_max_chars: int = 200
    open_docs: bool = True
    cors: CorsSettings = Field(default_factory=CorsSettings)


class ModelRef(BaseModel):
    """Reference to a (provider, model) pair."""

    provider: str
    model: str


class ModelsSettings(BaseModel):
    hard: ModelRef
    soft: ModelRef
    planner: ModelRef | None = None
    sql: ModelRef | None = None
    synthesizer: ModelRef | None = None
    verifier: ModelRef | None = None


class EmbeddingsSettings(BaseModel):
    provider: str
    model: str


class ProviderSettings(BaseModel):
    """Free-form per-provider config (api keys come from env, not here)."""

    base_url: str | None = None


class ProvidersSettings(BaseModel):
    openai: ProviderSettings = Field(default_factory=ProviderSettings)
    anthropic: ProviderSettings = Field(default_factory=ProviderSettings)
    google: ProviderSettings = Field(default_factory=ProviderSettings)
    openrouter: ProviderSettings = Field(default_factory=ProviderSettings)
    ollama: ProviderSettings = Field(
        default_factory=lambda: ProviderSettings(base_url="http://localhost:11434/v1")
    )
    lmstudio: ProviderSettings = Field(
        default_factory=lambda: ProviderSettings(base_url="http://localhost:1234/v1")
    )


class ConnectorsSettings(BaseModel):
    sql: SQLConnectorConfigUnion
    vector: VectorConnectorConfigUnion
    session_store: SessionStoreConfigUnion = Field(default_factory=SessionStoreInheritConfig)


class VerifierSettings(BaseModel):
    mode: Literal["off", "signal"] = "off"


class AgentSettings(BaseModel):
    style: str = "Responda de forma direta e profissional."
    language: str = "pt-BR"
    max_history_turns: int = 5
    turn_timeout_seconds: int = 120
    llm_timeout_seconds: int = 60
    sql_max_retries: int = 3
    sql_max_rows: int = 100
    text_search_k: int = 5


class IngestSettings(BaseModel):
    chunk_size: int = 1500
    chunk_overlap: int = 200


class SemanticMapSettings(BaseModel):
    regenerate_on_ingest: bool = True


class LoggingSettings(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["pretty", "json"] = "pretty"


class PathsSettings(BaseModel):
    """Where HARA writes/reads runtime artifacts (semantic maps, etc.).

    Connector storage paths (sqlite db, chromadb persist dir) live in
    [connectors.*]; this block covers the `data_dir` for non-connector
    artifacts produced by `hara semantic-map`.
    """

    data_dir: Path = Field(default_factory=lambda: Path("./data"))


# ---------- Root settings ----------


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=ServerSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    api: APISettings = Field(default_factory=APISettings)
    models: ModelsSettings
    embeddings: EmbeddingsSettings
    providers: ProvidersSettings = Field(default_factory=ProvidersSettings)
    connectors: ConnectorsSettings
    verifier: VerifierSettings = Field(default_factory=VerifierSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    semantic_map: SemanticMapSettings = Field(default_factory=SemanticMapSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    paths: PathsSettings = Field(default_factory=PathsSettings)

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_file=None,  # we load .env explicitly via load_dotenv
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,  # noqa: ARG003
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_file = getattr(cls, "_toml_file", None)
        if toml_file is not None and Path(toml_file).exists():
            return (
                init_settings,
                env_settings,
                TomlConfigSettingsSource(settings_cls, toml_file=toml_file),
                file_secret_settings,
            )
        return (init_settings, env_settings, file_secret_settings)


# Documented flat env vars (.env.example) → nested settings paths.
# Pydantic-Settings consumes nested fields via the `__` delimiter (AUTH__TOKEN).
# We bridge the user-facing flat names so copying .env.example just works.
_FLAT_ENV_ALIASES: dict[str, str] = {
    "HARA_API_TOKEN": "AUTH__TOKEN",
    "HARA_SQL_URL": "CONNECTORS__SQL__URL",
    "HARA_VECTOR_URL": "CONNECTORS__VECTOR__URL",
    "HARA_SESSION_URL": "CONNECTORS__SESSION_STORE__URL",
}


def _bridge_flat_env_vars() -> list[str]:
    """Promote documented flat HARA_* env vars to the nested form Pydantic-Settings reads.

    Returns the list of nested env var names that were set by this call so the
    caller can unset them after Settings() is instantiated. This keeps the
    mutation scoped to the load_settings() call — important for test isolation.
    """
    set_vars: list[str] = []
    for flat, nested in _FLAT_ENV_ALIASES.items():
        if flat in os.environ and nested not in os.environ:
            os.environ[nested] = os.environ[flat]
            set_vars.append(nested)
    return set_vars


def load_settings(toml_file: Path | str = "hara.toml") -> Settings:
    """Load settings from .env + hara.toml + env vars.

    Args:
        toml_file: Path to hara.toml. If not found, only env vars + defaults are used.

    Returns:
        Validated Settings instance.

    Raises:
        ValidationError: If required fields are missing or types mismatch.
    """
    load_dotenv()  # populate os.environ from .env so libs that read it directly work
    bridged = _bridge_flat_env_vars()  # HARA_API_TOKEN → AUTH__TOKEN, etc.

    # Hack: stuff toml_file path on the class attribute so settings_customise_sources sees it.
    # Pydantic-Settings doesn't accept ad-hoc init kwargs for sources, so this is the
    # cleanest way to inject a per-call TOML path without subclassing.
    Settings._toml_file = Path(toml_file)  # type: ignore[attr-defined]
    try:
        return Settings()  # pyright: ignore[reportCallIssue]
    finally:
        # why: Settings() consumes env vars at instantiation; once parsed we
        # can clean up the bridged forms so subsequent process work (or tests)
        # sees only the original env. The flat HARA_* vars stay untouched.
        for nested in bridged:
            os.environ.pop(nested, None)
