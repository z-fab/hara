"""`hara semantic-map` — generate / refresh structured.yaml and unstructured.yaml."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from langchain_core.language_models.chat_models import BaseChatModel
from rich.console import Console

from hara.config.settings import Settings, load_settings
from hara.connectors import (
    resolve_sql_connector_class,
    resolve_vector_connector_class,
)
from hara.providers import (
    resolve_embeddings_provider_class,
    resolve_llm_provider_class,
)
from hara.providers.resolver import resolve_node_model_ref
from hara.services.semantic_map import (
    build_structured_map,
    build_unstructured_map,
    current_snapshot,
    dump_structured_map,
    dump_unstructured_map,
    save_state,
    should_regenerate,
)
from hara.services.session_store import SessionStore, derive_session_dsn

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("semantic-map")
    def semantic_map_cmd(  # pyright: ignore[reportUnusedFunction]
        config: Path = typer.Option(  # noqa: B008
            Path("hara.toml"), "--config", "-c", help="Path to hara.toml."
        ),
        force: bool = typer.Option(
            False, "--force", help="Regenerate even if the snapshot is unchanged."
        ),
        data_dir: Path | None = typer.Option(  # noqa: B008
            None, "--data-dir", help="Override [paths].data_dir from hara.toml."
        ),
    ) -> None:
        """Generate (or refresh) structured.yaml and unstructured.yaml."""
        settings = load_settings(toml_file=config)
        effective_dir = data_dir if data_dir is not None else settings.paths.data_dir
        asyncio.run(_run(settings, force=force, data_dir=effective_dir))


async def _run(settings: Settings, *, force: bool, data_dir: Path) -> None:
    state_file = data_dir / ".semantic_map_state.json"
    store = SessionStore(dsn=derive_session_dsn(settings))
    await store.initialize()

    if not force and not await should_regenerate(store, state_file):
        console.print("[green]semantic maps up to date — nothing to do[/]")
        return

    sql_cls = resolve_sql_connector_class(settings.connectors.sql.type)
    sql = sql_cls.from_config(settings.connectors.sql)

    emb_cls = resolve_embeddings_provider_class(settings.embeddings.provider)
    emb = emb_cls.from_config(
        settings.embeddings.model,
        getattr(settings.providers, settings.embeddings.provider),
    )
    vec_cls = resolve_vector_connector_class(settings.connectors.vector.type)
    vec = vec_cls.from_config(settings.connectors.vector, embedder=emb)

    # Use the *soft* model for descriptions: cheap, batchable, low-stakes.
    # Note: resolver signature is (node, settings_models) — node first.
    llm = _build_llm(settings)

    console.print("Generating structured.yaml…")
    structured_result = await build_structured_map(sql, llm)

    console.print("Generating unstructured.yaml…")
    unstructured_result = await build_unstructured_map(vec, llm)

    # Transactional publish: build BOTH maps fully in memory first, then
    # only touch the live ``.yaml`` files when both builders reported zero
    # failures. A transient LLM error must not overwrite a previously
    # good map with a partial one. On partial failure we (optionally)
    # write ``.partial.yaml`` siblings for debugging and skip the state
    # save so the next non-``--force`` run retries.
    structured_path = data_dir / "structured.yaml"
    unstructured_path = data_dir / "unstructured.yaml"
    if structured_result.failed_ids or unstructured_result.failed_ids:
        console.print("[yellow]warning: omitted from map due to errors:[/]")
        for ident in structured_result.failed_ids:
            console.print(f"  - table {ident}")
        for ident in unstructured_result.failed_ids:
            console.print(f"  - doc {ident}")
        # Best-effort debug artifact; live YAMLs untouched.
        dump_structured_map(structured_result.data, data_dir / "structured.partial.yaml")
        dump_unstructured_map(unstructured_result.data, data_dir / "unstructured.partial.yaml")
        console.print(
            "[yellow]not saving state-lock — will retry on next run; "
            "partial output written to *.partial.yaml[/]"
        )
    else:
        dump_structured_map(structured_result.data, structured_path)
        dump_unstructured_map(unstructured_result.data, unstructured_path)
        save_state(state_file, await current_snapshot(store))
        console.print(f"[green]done — wrote {structured_path} and {unstructured_path}[/]")


def _build_llm(settings: Settings) -> BaseChatModel:
    """Resolve the synthesizer-tier LLM. Extracted for easy patching in tests."""
    ref = resolve_node_model_ref("synthesizer", settings.models)
    cls = resolve_llm_provider_class(ref.provider)
    return cls.from_config(ref.model, getattr(settings.providers, ref.provider))
