"""Tests for the `hara chat` CLI — one-shot mode (REPL covered in Task 22)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from hara.agent.orchestrator import TurnResult
from hara.cli.app import app

runner = CliRunner()


def _patch_orchestrator(monkeypatch, turn_result_obj: Any) -> None:
    """Stub build_orchestrator_from_settings + Orchestrator.run_turn."""
    fake_orch = MagicMock()
    fake_orch.run_turn = AsyncMock(return_value=turn_result_obj)

    async def _fake_factory(settings, *, data_dir):
        return fake_orch

    monkeypatch.setattr("hara.cli.commands.chat.build_orchestrator_from_settings", _fake_factory)
    return fake_orch


def _make_turn_result(answer: str = "resposta", citations=None, metadata=None) -> Any:
    return TurnResult(
        thread_id="thr_x",
        turn_id="trn_x",
        answer=answer,
        citations=citations or [],
        metadata=metadata
        or {
            "routing": {"subqueries_count": 0, "routes_taken": []},
            "verifier": None,
            "tokens": {"input": 0, "output": 0, "total": 0},
            "latency_per_node": {},
            "unsupported_markers": 0,
        },
    )


def test_chat_oneshot_prints_answer(monkeypatch, tmp_path) -> None:
    _patch_orchestrator(monkeypatch, _make_turn_result(answer="MT produziu 100t."))
    monkeypatch.setattr("hara.cli.commands.chat.load_settings", lambda toml_file: MagicMock())

    result = runner.invoke(app, ["chat", "Quanto produziu MT?", "--no-stream"])
    assert result.exit_code == 0
    assert "MT produziu 100t." in result.stdout


def test_chat_oneshot_renders_citations(monkeypatch, tmp_path) -> None:
    citations = [
        {
            "evidence_id": 1,
            "kind": "sql",
            "source": "producao",
            "section": "",
            "snippet": "MT, 100",
        },
    ]
    _patch_orchestrator(monkeypatch, _make_turn_result(citations=citations))
    monkeypatch.setattr("hara.cli.commands.chat.load_settings", lambda toml_file: MagicMock())

    result = runner.invoke(app, ["chat", "x", "--no-stream"])
    assert result.exit_code == 0
    assert "producao" in result.stdout


def test_chat_passes_thread_id(monkeypatch, tmp_path) -> None:
    fake_orch = _patch_orchestrator(monkeypatch, _make_turn_result())
    monkeypatch.setattr("hara.cli.commands.chat.load_settings", lambda toml_file: MagicMock())

    result = runner.invoke(app, ["chat", "x", "--thread-id", "thr_abc", "--no-stream"])
    assert result.exit_code == 0
    fake_orch.run_turn.assert_awaited_once()
    call_kwargs = fake_orch.run_turn.await_args.kwargs
    assert call_kwargs.get("thread_id") == "thr_abc"


def test_chat_no_question_no_repl_in_test_env(monkeypatch, tmp_path) -> None:
    """Without question and without TTY, command exits cleanly with status 0."""
    monkeypatch.setattr("hara.cli.commands.chat.load_settings", lambda toml_file: MagicMock())
    _patch_orchestrator(monkeypatch, _make_turn_result())

    result = runner.invoke(app, ["chat", "--no-stream"], input="")
    assert result.exit_code == 0


def test_chat_handles_keyboard_interrupt_in_oneshot(monkeypatch) -> None:
    """Ctrl-C in one-shot exits with code 130 (UNIX convention)."""
    fake_orch = MagicMock()

    async def _interrupt(*a, **kw):
        raise KeyboardInterrupt

    fake_orch.run_turn = _interrupt

    async def _f(s, *, data_dir):
        return fake_orch

    monkeypatch.setattr("hara.cli.commands.chat.build_orchestrator_from_settings", _f)
    monkeypatch.setattr("hara.cli.commands.chat.load_settings", lambda toml_file: MagicMock())

    result = runner.invoke(app, ["chat", "x", "--no-stream"])
    assert result.exit_code == 130
