"""`hara doctor` — config validation + connectivity checks.

Three sections:
  1. Config: hara.toml parses, required fields present, .env loaded.
  2. Connectors: SQL and Vector instantiate via from_config + health_check.
  3. Providers: each configured LLM/embedding provider is importable + has API key.

Live API roundtrip is NOT performed in v0.1's `doctor` (cost + latency).
A future flag `--live` will hit each provider with 1-token call.

The check logic is exposed as :func:`run_checks` — pure, async, no Rich —
so both the CLI command and the API route ``GET /doctor`` can share it.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from hara.config.settings import Settings, load_settings
from hara.connectors import (
    resolve_sql_connector_class,
    resolve_vector_connector_class,
)
from hara.providers import (
    resolve_embeddings_provider_class,
)
from hara.providers.resolver import resolve_node_model_ref

_console = Console()


@dataclass(frozen=True)
class CheckResult:
    """One row of the doctor output. Used by both the CLI and the API."""

    check: str  # e.g. "config", "sql (sqlite)", "node:planner"
    status: str  # "OK" | "WARN" | "FAIL"
    detail: str  # human-readable detail/message


async def run_checks(settings: Settings) -> list[CheckResult]:
    """Run all doctor checks and return structured results.

    Both ``hara doctor`` (CLI) and ``GET /doctor`` (API) consume this.
    No I/O wrapping in Rich here — the caller renders.
    """
    results: list[CheckResult] = []
    results.append(CheckResult(check="config", status="OK", detail="hara.toml"))
    results.extend(await _check_sql_async(settings))
    results.extend(await _check_vector_async(settings))
    results.extend(_check_providers(settings))
    results.extend(_check_semantic_maps(settings.paths.data_dir))
    results.extend(_check_node_models(settings))
    return results


def doctor_command(
    config: Path = typer.Option(  # noqa: B008
        Path("hara.toml"),
        "--config",
        "-c",
        help="Path to hara.toml",
    ),
) -> None:
    """Validate configuration and test connectivity."""
    try:
        settings = load_settings(toml_file=config)
    except Exception as e:
        _console.print(f"[red]config FAIL: {e}[/]")
        raise typer.Exit(code=1) from e

    table = Table(title="HARA Doctor")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Detail", style="dim")

    results = asyncio.run(run_checks(settings))
    overall_ok = True
    style_map = {"OK": "green", "WARN": "yellow", "FAIL": "red"}
    for r in results:
        # The first row's detail should reflect the actual config path —
        # run_checks doesn't know the path, so we patch it here.
        detail = str(config) if r.check == "config" and r.status == "OK" else r.detail
        style = style_map.get(r.status, "white")
        table.add_row(r.check, f"[{style}]{r.status}[/]", detail)
        if r.status == "FAIL":
            overall_ok = False

    _console.print(table)
    if not overall_ok:
        raise typer.Exit(code=1)


async def _check_sql_async(settings: Settings) -> list[CheckResult]:
    label = f"sql ({settings.connectors.sql.type})"
    try:
        cls = resolve_sql_connector_class(settings.connectors.sql.type)
        connector = cls.from_config(settings.connectors.sql)
        health = await connector.health_check()
        if health.ok:
            return [CheckResult(check=label, status="OK", detail=health.message)]
        return [CheckResult(check=label, status="FAIL", detail=health.message)]
    except Exception as e:
        return [CheckResult(check=label, status="FAIL", detail=str(e))]


async def _check_vector_async(settings: Settings) -> list[CheckResult]:
    label = f"vector ({settings.connectors.vector.type})"
    try:
        cls = resolve_vector_connector_class(settings.connectors.vector.type)
        # We need an embedder; instantiate the configured one.
        emb_cls = resolve_embeddings_provider_class(settings.embeddings.provider)
        emb = emb_cls.from_config(
            settings.embeddings.model,
            getattr(settings.providers, settings.embeddings.provider),
        )
        connector = cls.from_config(settings.connectors.vector, embedder=emb)
        health = await connector.health_check()
        if health.ok:
            return [CheckResult(check=label, status="OK", detail=health.message)]
        return [CheckResult(check=label, status="FAIL", detail=health.message)]
    except Exception as e:
        return [CheckResult(check=label, status="FAIL", detail=str(e))]


def _check_providers(settings: Settings) -> list[CheckResult]:
    """Check that each configured provider's key is set in env (no API call)."""
    results: list[CheckResult] = []
    needed: set[str] = {settings.models.hard.provider, settings.models.soft.provider}
    for node_attr in ("planner", "sql", "synthesizer", "verifier"):
        ref = getattr(settings.models, node_attr, None)
        if ref is not None:
            needed.add(ref.provider)
    needed.add(settings.embeddings.provider)

    key_env = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    for provider in sorted(needed):
        label = f"provider ({provider})"
        env_var = key_env.get(provider)
        if env_var is None:
            # ollama / lmstudio don't need key
            results.append(CheckResult(check=label, status="OK", detail="no key needed"))
            continue
        if os.environ.get(env_var):
            results.append(CheckResult(check=label, status="OK", detail=f"{env_var} set"))
        else:
            results.append(CheckResult(check=label, status="FAIL", detail=f"{env_var} missing"))
    return results


def _check_semantic_maps(data_dir: Path) -> list[CheckResult]:
    """Warn (not fail) when structured.yaml or unstructured.yaml are absent
    in ``data_dir`` — chat can run without them, Planner just sees empty maps.
    """
    results: list[CheckResult] = []
    for name in ("structured.yaml", "unstructured.yaml"):
        path = data_dir / name
        label = f"semantic-map ({name})"
        if path.exists():
            results.append(CheckResult(check=label, status="OK", detail=str(path)))
        else:
            results.append(
                CheckResult(
                    check=label,
                    status="WARN",
                    detail=f"ausente em {path} — rode `hara semantic-map`",
                )
            )
    return results


def _check_node_models(settings: Settings) -> list[CheckResult]:
    """Resolve the (provider, model) pair for each agent node — pure config
    check (no API call). Catches misconfigured [models.<node>] overrides early.
    """
    results: list[CheckResult] = []
    for node in ("planner", "sql", "synthesizer", "verifier"):
        label = f"node:{node}"
        try:
            ref = resolve_node_model_ref(node, settings.models)
            results.append(
                CheckResult(check=label, status="OK", detail=f"{ref.provider}:{ref.model}")
            )
        except Exception as e:
            results.append(
                CheckResult(check=label, status="FAIL", detail=f"resolution failed: {e}")
            )
    return results
