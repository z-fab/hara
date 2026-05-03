"""`hara ingest` — multimodal data ingestion command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

# Imported at module scope so unittest.mock.patch can intercept the call.
# A function-local import would resolve once at call time and bypass the patch.
from hara.cli.commands.semantic_map import (
    _run as _run_semantic_map,  # pyright: ignore[reportPrivateUsage]
)
from hara.config.settings import Settings, load_settings
from hara.connectors import (
    resolve_sql_connector_class,
    resolve_vector_connector_class,
)
from hara.ingest.pipelines.structured import StructuredItemStatus
from hara.ingest.pipelines.unstructured import UnstructuredItemStatus
from hara.ingest.progress import ProgressCallback, ProgressEvent
from hara.ingest.service import IngestReport, run_ingest
from hara.providers import resolve_embeddings_provider_class
from hara.services.session_store import SessionStore, derive_session_dsn

console = Console()


def register(app: typer.Typer) -> None:
    """Register the `hara ingest` command on `app`.

    Each Typer command in this CLI takes its own ``--config`` flag — there
    is no global callback. Mirrors the ``doctor`` command pattern from the
    Plano 1 scaffolding.
    """

    @app.command("ingest")
    def ingest_cmd(  # pyright: ignore[reportUnusedFunction]
        from_path: Path = typer.Option(  # noqa: B008
            ..., "--from", help="File or directory to ingest."
        ),
        config: Path = typer.Option(  # noqa: B008
            Path("hara.toml"), "--config", "-c", help="Path to hara.toml."
        ),
        target: str = typer.Option("auto", "--target", help="auto | sql | vector (default: auto)."),
        on_conflict: str = typer.Option(
            "skip", "--on-conflict", help="skip | replace | append (default: skip)."
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help="Abort on the first failed file (parser, pipeline, or scanner).",
        ),
    ) -> None:
        """Ingest CSV/PDF/DOCX/MD/TXT files into SQL/Vector connectors."""
        if on_conflict not in ("skip", "replace", "append"):
            raise typer.BadParameter("--on-conflict must be skip|replace|append")
        if target not in ("auto", "sql", "vector"):
            raise typer.BadParameter("--target must be auto|sql|vector")

        settings = load_settings(toml_file=config)
        report = asyncio.run(
            _run(
                settings,
                root=from_path,
                on_conflict=on_conflict,
                target=target,
                strict=strict,
            )
        )

        _render_report(from_path, report)

        any_ok = any(i.status == StructuredItemStatus.OK for i in report.structured.items) or any(
            i.status == UnstructuredItemStatus.OK for i in report.unstructured.items
        )

        if any_ok and settings.semantic_map.regenerate_on_ingest:
            # Don't pass force=True — let should_regenerate compare snapshots.
            # The auto-trigger only runs after at least one successful ingest,
            # so the snapshot will (almost always) differ; should_regenerate
            # short-circuits the no-op edge case (e.g., re-ingest with skip).
            asyncio.run(_run_semantic_map(settings, force=False, data_dir=Path("./data")))

        if not any_ok:
            raise typer.Exit(code=1)


def _make_progress_callback() -> ProgressCallback:
    icons = {"done": "✓", "skipped": "↷", "failed": "✗"}
    styles = {"done": "green", "skipped": "yellow", "failed": "red"}

    def cb(event: ProgressEvent) -> None:
        prefix = "SQL" if event.pipeline == "sql" else "VEC"
        tag = f"[{prefix} {event.index}/{event.total}]"
        name = event.file
        if event.stage in ("done", "skipped", "failed"):
            icon = icons.get(event.stage, "·")
            style = styles[event.stage]
            detail = f" {event.detail}" if event.detail else ""
            console.print(f"{tag} {name} [{style}]{icon}{detail}[/]")
        else:
            console.print(f"[dim]{tag} {name} {event.stage}...[/]")

    return cb


async def _run(
    settings: Settings,
    *,
    root: Path,
    on_conflict: str,
    target: str,
    strict: bool,
    progress: ProgressCallback | None = None,
) -> IngestReport:
    sql_cls = resolve_sql_connector_class(settings.connectors.sql.type)
    sql = sql_cls.from_config(settings.connectors.sql)

    emb_cls = resolve_embeddings_provider_class(settings.embeddings.provider)
    emb = emb_cls.from_config(
        settings.embeddings.model,
        getattr(settings.providers, settings.embeddings.provider),
    )
    vec_cls = resolve_vector_connector_class(settings.connectors.vector.type)
    vec = vec_cls.from_config(settings.connectors.vector, embedder=emb)

    store = SessionStore(dsn=derive_session_dsn(settings))
    await store.initialize()

    cb = progress if progress is not None else _make_progress_callback()
    return await run_ingest(
        root=root,
        sql_connector=sql,
        vector_connector=vec,
        store=store,
        on_conflict=on_conflict,  # type: ignore[arg-type]
        target=target,  # type: ignore[arg-type]
        strict=strict,
        chunk_size=settings.ingest.chunk_size,
        chunk_overlap=settings.ingest.chunk_overlap,
        progress=cb,
    )


def _render_report(root: Path, report: IngestReport) -> None:
    console.print(f"Scanning {root}...")
    console.print(f"Found: {report.scanned} dispatchable, {report.ignored} ignored\n")
    if report.structured.items:
        table = Table(title="Structured (CSV → SQL)")
        table.add_column("file")
        table.add_column("table")
        table.add_column("rows")
        table.add_column("status")
        for i in report.structured.items:
            table.add_row(i.relative_path, i.table_name, str(i.rows), i.status.value)
        console.print(table)
    if report.unstructured.items:
        table = Table(title="Unstructured (→ Vector)")
        table.add_column("file")
        table.add_column("file_id")
        table.add_column("chunks")
        table.add_column("tokens")
        table.add_column("status")
        for i in report.unstructured.items:
            table.add_row(
                i.relative_path,
                i.file_id,
                str(i.chunks),
                str(i.tokens or "—"),
                i.status.value,
            )
        console.print(table)
