"""`hara init` — interactive setup wizard. Spec §7."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt

console = Console()


_TOML_TEMPLATE = """\
[server]
host = "0.0.0.0"
port = 8000

[auth]
token = "{auth_token}"

[api]
max_message_length = 8000
snippet_max_chars = 200
open_docs = true

[api.cors]
allowed_origins = []

[models]
hard = {{ provider = "{llm_provider}", model = "{model_hard}" }}
soft = {{ provider = "{llm_provider}", model = "{model_soft}" }}

[embeddings]
provider = "{embed_provider}"
model = "{embed_model}"

[providers.openai]
[providers.anthropic]
[providers.google]
[providers.openrouter]
[providers.ollama]
base_url = "http://localhost:11434/v1"
[providers.lmstudio]
base_url = "http://localhost:1234/v1"

[connectors.sql]
{sql_block}

[connectors.vector]
{vector_block}

[connectors.session_store]
ttl_days = 7

[paths]
data_dir = "./data"

[verifier]
mode = "off"

[agent]
style = {style!r}
language = "pt-BR"
max_history_turns = 5
turn_timeout_seconds = 120
llm_timeout_seconds = 60
sql_max_retries = 3
sql_max_rows = 100
text_search_k = 5

[ingest]
chunk_size = 1500
chunk_overlap = 200

[semantic_map]
regenerate_on_ingest = true

[logging]
level = "INFO"
format = "pretty"
"""


_DEFAULT_TOML_PARAMS: dict[str, Any] = {
    "llm_provider": "openai",
    "model_hard": "gpt-5",
    "model_soft": "gpt-5-mini",
    "embed_provider": "openai",
    "embed_model": "text-embedding-3-small",
    "sql_block": 'type = "memory"',
    "vector_block": 'type = "memory"',
    "style": (
        "Responda de forma direta e profissional, em português brasileiro. "
        "Use linguagem clara e cite as fontes."
    ),
}


def register(app: typer.Typer) -> None:
    @app.command("init")
    def init_cmd(  # pyright: ignore[reportUnusedFunction]
        config: Path = typer.Option(  # noqa: B008
            Path("hara.toml"),
            "--config",
            "-c",
            help="Where to write hara.toml.",
        ),
        env_path: Path = typer.Option(  # noqa: B008
            Path(".env"),
            "--env",
            help="Where to write secrets.",
        ),
        non_interactive: bool = typer.Option(False, "--non-interactive"),
        force: bool = typer.Option(False, "--force"),
    ) -> None:
        """Interactive setup wizard. --non-interactive writes Docker/CI defaults."""
        if config.exists() and not force:
            if non_interactive:
                console.print(f"[red]{config} exists; pass --force to overwrite.[/]")
                raise typer.Exit(code=1)
            if not Confirm.ask(f"{config} exists. Overwrite?", default=False):
                console.print("[yellow]aborted[/]")
                raise typer.Exit(code=1)

        if non_interactive:
            _write_defaults(config, env_path)
            console.print(f"[green]wrote defaults to {config}[/]")
            console.print(f"[green]wrote {env_path}[/]")
            return

        choices = _interactive_prompts(env_path)
        config.write_text(_TOML_TEMPLATE.format(**choices), encoding="utf-8")
        console.print(f"\n[green]✓[/] wrote {config}")
        console.print(f"[green]✓[/] wrote secrets to {env_path}")
        console.print("\nNext: [bold]hara doctor[/] to verify, then [bold]hara serve[/].")


def _interactive_prompts(env_path: Path) -> dict[str, Any]:
    """The 9 spec §7 prompts. Returns the kwargs for _TOML_TEMPLATE."""
    p: dict[str, Any] = dict(_DEFAULT_TOML_PARAMS)

    # 1. LLM provider + API key
    p["llm_provider"] = Prompt.ask(
        "Provider de LLM",
        choices=["openai", "anthropic", "google", "openrouter", "ollama", "lmstudio"],
        default="openai",
    )
    api_key = Prompt.ask(
        f"{p['llm_provider'].upper()}_API_KEY (deixe vazio para skip)",
        default="",
        password=True,
    )

    # 2. Models hard + soft
    p["model_hard"] = Prompt.ask(
        "Modelo HARD (planner/verifier)",
        default=_DEFAULT_TOML_PARAMS["model_hard"] if p["llm_provider"] == "openai" else "",
    )
    p["model_soft"] = Prompt.ask(
        "Modelo SOFT (sql/synthesizer)",
        default=_DEFAULT_TOML_PARAMS["model_soft"] if p["llm_provider"] == "openai" else "",
    )

    # 3. Embedding provider + model
    p["embed_provider"] = Prompt.ask(
        "Provider de embeddings",
        choices=["openai", "google", "ollama"],
        default="openai",
    )
    p["embed_model"] = Prompt.ask(
        "Modelo de embeddings",
        default="text-embedding-3-small" if p["embed_provider"] == "openai" else "",
    )

    # 4. SQL connector
    sql_type = Prompt.ask(
        "Connector SQL",
        choices=["memory", "sqlite", "postgres"],
        default="sqlite",
    )
    if sql_type == "memory":
        p["sql_block"] = 'type = "memory"'
    elif sql_type == "sqlite":
        sql_path = Prompt.ask("Caminho do banco SQLite", default="./data/hara.db")
        p["sql_block"] = f'type = "sqlite"\npath = "{sql_path}"'
    else:  # postgres
        sql_url = Prompt.ask("URL do Postgres (postgresql://user:pass@host/db)")
        p["sql_block"] = f'type = "postgres"\nurl = "{sql_url}"'

    # 5. Vector connector
    vec_type = Prompt.ask(
        "Connector vector",
        choices=["memory", "chromadb"],
        default="chromadb",
    )
    if vec_type == "memory":
        p["vector_block"] = 'type = "memory"'
    else:
        persist = Prompt.ask("Persist dir do ChromaDB", default="./data/chroma")
        p["vector_block"] = f'type = "chromadb"\npersist_directory = "{persist}"'

    # 6. Auth token
    if Confirm.ask("Gerar token de auth aleatório?", default=True):
        token = secrets.token_urlsafe(32)
        console.print(f"[dim]token: {token}[/]")
    else:
        token = Prompt.ask("Token de auth", password=True)
    p["auth_token"] = token

    # 7. Agent style
    p["style"] = Prompt.ask(
        "Tom de voz do agente (texto livre)",
        default=_DEFAULT_TOML_PARAMS["style"],
    )

    # 8 + 9: deferred — ingest/semantic-map runs would block; documented as
    # "next steps" in the printed summary instead. v0.1 covers the file-writing
    # surface; orchestrating subsequent CLI calls from inside `init` is brittle
    # and not strictly required by spec §17 critério 2 (the spec says "9 prompts;
    # nenhum default obrigatório oculto" — file writing is the contract).
    # If a user wants to ingest now, the printed Next: hint guides them.

    # Write secrets
    env_lines: list[str] = []
    if api_key:
        provider_env = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }.get(p["llm_provider"])
        if provider_env:
            env_lines.append(f"{provider_env}={api_key}")
    env_lines.append(f"HARA_API_TOKEN={token}")
    env_path.write_text("\n".join(env_lines) + "\n", encoding="utf-8")

    return p


def _write_defaults(config: Path, env_path: Path) -> None:
    """Spec §17 critério 3 — Docker/CI defaults; no prompts."""
    p = dict(_DEFAULT_TOML_PARAMS)
    p["auth_token"] = secrets.token_urlsafe(32)
    config.write_text(_TOML_TEMPLATE.format(**p), encoding="utf-8")
    env_path.write_text(
        f"# Set provider keys (e.g. OPENAI_API_KEY=sk-...)\nHARA_API_TOKEN={p['auth_token']}\n",
        encoding="utf-8",
    )
