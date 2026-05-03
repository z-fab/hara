"""CLI plumbing helpers for the agent — kept out of orchestrator to stay
focused on graph composition.

`build_orchestrator_from_settings` is the one-call factory the chat
command uses. `load_semantic_maps` is also useful in `hara doctor`
and tests, hence exposed separately.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.orchestrator import Orchestrator
from hara.config.settings import Settings
from hara.connectors import (
    resolve_sql_connector_class,
    resolve_vector_connector_class,
)
from hara.providers import (
    resolve_embeddings_provider_class,
    resolve_llm_provider_class,
)
from hara.providers.resolver import resolve_node_model_ref
from hara.services.session_store import SessionStore, derive_session_dsn


def load_semantic_maps(data_dir: Path) -> tuple[str, str]:
    """Return (structured_yaml_text, unstructured_yaml_text). Empty when missing."""
    s_path = data_dir / "structured.yaml"
    u_path = data_dir / "unstructured.yaml"
    s = s_path.read_text(encoding="utf-8") if s_path.exists() else ""
    u = u_path.read_text(encoding="utf-8") if u_path.exists() else ""
    return s, u


def _resolve_llm(node: str, settings: Settings) -> BaseChatModel:
    ref = resolve_node_model_ref(node, settings.models)
    cls = resolve_llm_provider_class(ref.provider)
    return cls.from_config(ref.model, getattr(settings.providers, ref.provider))


def _resolve_embedder(settings: Settings) -> Embeddings:
    cls = resolve_embeddings_provider_class(settings.embeddings.provider)
    return cls.from_config(
        settings.embeddings.model,
        getattr(settings.providers, settings.embeddings.provider),
    )


async def build_orchestrator_from_settings(settings: Settings, *, data_dir: Path) -> Orchestrator:
    """Resolve all dependencies and return a ready Orchestrator.

    Caller is responsible for the lifetime of the SessionStore (file handle
    is held only inside its async methods, so single ownership suffices).
    """
    structured_yaml, unstructured_yaml = load_semantic_maps(data_dir)

    sql_cls = resolve_sql_connector_class(settings.connectors.sql.type)
    sql_conn = sql_cls.from_config(settings.connectors.sql)

    embedder = _resolve_embedder(settings)
    vec_cls = resolve_vector_connector_class(settings.connectors.vector.type)
    vec_conn = vec_cls.from_config(settings.connectors.vector, embedder=embedder)

    store = SessionStore(dsn=derive_session_dsn(settings))
    await store.initialize()

    return Orchestrator(
        session_store=store,
        sql_connector=sql_conn,
        vec_connector=vec_conn,
        planner_llm=_resolve_llm("planner", settings),
        sql_llm=_resolve_llm("sql", settings),
        synthesizer_llm=_resolve_llm("synthesizer", settings),
        verifier_llm=_resolve_llm("verifier", settings),
        structured_map_yaml=structured_yaml,
        unstructured_map_yaml=unstructured_yaml,
        agent_style=settings.agent.style,
        text_search_k=settings.agent.text_search_k,
        sql_max_retries=settings.agent.sql_max_retries,
        sql_max_rows=settings.agent.sql_max_rows,
        verifier_mode=settings.verifier.mode,
        max_history_turns=settings.agent.max_history_turns,
    )
