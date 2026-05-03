"""`hara connectors list` and `hara providers list` — discoverability."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from hara.connectors import list_sql_connectors, list_vector_connectors
from hara.providers import list_embeddings_providers, list_llm_providers

connectors_app = typer.Typer(name="connectors", help="Inspect installed connectors")
providers_app = typer.Typer(name="providers", help="Inspect installed providers")
_console = Console()


@connectors_app.command("list")
def connectors_list() -> None:
    """List built-in and plugin connectors available in this install."""
    table = Table(title="Connectors")
    table.add_column("Kind", style="cyan")
    table.add_column("Name", style="green")

    for name in sorted(list_sql_connectors()):
        table.add_row("sql", name)
    for name in sorted(list_vector_connectors()):
        table.add_row("vector", name)
    _console.print(table)


@providers_app.command("list")
def providers_list() -> None:
    """List built-in and plugin LLM/embeddings providers."""
    table = Table(title="Providers")
    table.add_column("Kind", style="cyan")
    table.add_column("Name", style="green")

    for name in sorted(list_llm_providers()):
        table.add_row("llm", name)
    for name in sorted(list_embeddings_providers()):
        table.add_row("embeddings", name)
    _console.print(table)
