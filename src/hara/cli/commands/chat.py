"""`hara chat` — one-shot or REPL conversation with the agent.

One-shot: `hara chat "pergunta"` runs a single turn, streams to stdout
(or prints whole answer if --no-stream), exits.

REPL: `hara chat` with no question opens an interactive session. Each
prompt is one turn in the same thread; Ctrl-D / `/exit` quits.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from hara.agent.orchestrator import Orchestrator, TurnResult
from hara.agent.setup import build_orchestrator_from_settings
from hara.config.settings import load_settings

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("chat")
    def chat_cmd(  # pyright: ignore[reportUnusedFunction]
        question: str | None = typer.Argument(
            None, help="Pergunta single-shot. Sem argumento → REPL."
        ),
        config: Path = typer.Option(  # noqa: B008
            Path("hara.toml"), "--config", "-c", help="hara.toml path."
        ),
        data_dir: Path | None = typer.Option(  # noqa: B008
            None,
            "--data-dir",
            help="Override [paths].data_dir from hara.toml (structured.yaml + unstructured.yaml).",
        ),
        thread_id: str | None = typer.Option(
            None, "--thread-id", help="Continuar uma thread existente."
        ),
        no_stream: bool = typer.Option(
            False, "--no-stream", help="Imprimir só a resposta final, sem streaming."
        ),
    ) -> None:
        """Conversa com o agente (one-shot ou REPL)."""
        settings = load_settings(toml_file=config)
        effective_dir = data_dir if data_dir is not None else settings.paths.data_dir
        if question is not None:
            asyncio.run(
                _run_one_shot(
                    settings=settings,
                    data_dir=effective_dir,
                    question=question,
                    thread_id=thread_id,
                    stream=not no_stream,
                )
            )
        else:
            asyncio.run(
                _run_repl(
                    settings=settings,
                    data_dir=effective_dir,
                    thread_id=thread_id,
                    stream=not no_stream,
                )
            )


async def _run_one_shot(
    *,
    settings: Any,
    data_dir: Path,
    question: str,
    thread_id: str | None,
    stream: bool,
) -> None:
    orch = await build_orchestrator_from_settings(settings, data_dir=data_dir)
    try:
        result = await _execute_turn(orch, question, thread_id=thread_id, stream=stream)
    except KeyboardInterrupt:
        console.print("\n[yellow]cancelado[/]")
        raise typer.Exit(code=130) from None
    _render_citations(result)
    _render_metadata(result)
    console.print(f"[dim]thread_id: {result.thread_id}[/dim]")


async def _run_repl(
    *,
    settings: Any,
    data_dir: Path,
    thread_id: str | None,
    stream: bool,
) -> None:
    if not sys.stdin.isatty():
        # Non-interactive (test, pipe) — exit cleanly.
        console.print(
            '[yellow]hara chat: nada para ler em stdin não-tty. Use `hara chat "pergunta"`.[/]'
        )
        return

    orch = await build_orchestrator_from_settings(settings, data_dir=data_dir)
    console.print("[bold green]hara chat[/] — REPL. /exit para sair.")
    if thread_id:
        console.print(f"[dim]continuando thread {thread_id}[/dim]")

    while True:
        try:
            q = console.input("[bold cyan]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not q:
            continue
        if q in {"/exit", "/quit"}:
            break
        if q == "/reset":
            thread_id = None
            console.print("[dim]nova thread no próximo turno[/dim]")
            continue

        try:
            result = await _execute_turn(orch, q, thread_id=thread_id, stream=stream)
        except KeyboardInterrupt:
            console.print("\n[yellow]turno cancelado — continue ou /exit[/]")
            continue
        thread_id = result.thread_id  # carry forward
        _render_citations(result)


async def _execute_turn(
    orch: Orchestrator,
    question: str,
    *,
    thread_id: str | None,
    stream: bool,
) -> TurnResult:
    if stream:
        # Three visual stages share one Live region (no flicker, no double
        # cursor jumps):
        #   1. spinner "Pensando…" while Planner / SQL / Text run
        #   2. plain Text streaming as Synthesizer tokens arrive (markdown
        #      mid-stream renders weird — closing markers may not be there yet)
        #   3. Markdown re-render at end so headers / lists / code-blocks
        #      come out nicely formatted.
        text_buf = Text()
        spinner: Spinner = Spinner("dots", text=Text(" Pensando…", style="dim"))
        first_token_seen = False
        with Live(spinner, console=console, refresh_per_second=20) as live:

            def _on_token(token: str) -> None:
                nonlocal first_token_seen
                if not first_token_seen:
                    first_token_seen = True
                    live.update(text_buf)
                text_buf.append(token)
                live.update(text_buf)

            result = await orch.run_turn(question, thread_id=thread_id, on_token=_on_token)
            if result.answer:
                live.update(Markdown(result.answer))
        return result

    # No-stream: spinner during the whole call, then render Markdown.
    with console.status("[dim]Pensando…[/]", spinner="dots"):
        result = await orch.run_turn(question, thread_id=thread_id)
    if result.answer:
        console.print(Markdown(result.answer))
    return result


def _render_citations(result: TurnResult) -> None:
    if not result.citations:
        return
    table = Table(title="Citações", show_lines=False)
    table.add_column("ref", style="cyan")
    table.add_column("kind")
    table.add_column("source")
    table.add_column("snippet", overflow="fold")
    for c in result.citations:
        table.add_row(
            f"<ref:{c['evidence_id']}>",
            c["kind"],
            c["source"],
            c["snippet"][:120],
        )
    console.print(table)


def _render_metadata(result: TurnResult) -> None:
    md = result.metadata
    routing = md.get("routing", {})
    bits: list[str] = []
    bits.append(f"subqueries={routing.get('subqueries_count', 0)}")
    routes = routing.get("routes_taken", [])
    if routes:
        bits.append(f"routes={','.join(routes)}")
    if md.get("unsupported_markers", 0):
        bits.append(f"unsupported_markers={md['unsupported_markers']}")
    ver = md.get("verifier")
    if ver is not None:
        bits.append(
            f"verifier={'pass' if ver.get('overall_pass') else 'fail'} "
            f"pct={ver.get('pct_supported', 0):.2f}"
        )
    if bits:
        console.print(f"[dim]{' | '.join(bits)}[/dim]")
