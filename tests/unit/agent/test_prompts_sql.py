"""Tests for the SQL generator prompt builder."""

from __future__ import annotations

from hara.agent.prompts.sql import build_sql_prompt, strip_sql_fences


def test_sql_prompt_includes_question_and_dialect() -> None:
    prompt = build_sql_prompt(
        question="Quais 5 estados produzem mais soja?",
        dialect="sqlite",
        structured_map_yaml="tables:\n  - name: producao\n",
        previous_error=None,
    )
    assert "Quais 5 estados produzem mais soja?" in prompt
    assert "sqlite" in prompt.lower()


def test_sql_prompt_includes_structured_map() -> None:
    prompt = build_sql_prompt(
        question="x",
        dialect="sqlite",
        structured_map_yaml="tables:\n  - name: producao\n    columns: [uf]\n",
        previous_error=None,
    )
    assert "producao" in prompt


def test_sql_prompt_includes_previous_error_on_retry() -> None:
    prompt = build_sql_prompt(
        question="x",
        dialect="sqlite",
        structured_map_yaml="",
        previous_error="no such table: produkao",
    )
    assert "produkao" in prompt
    assert "previous_error" in prompt or "erro anterior" in prompt.lower()


def test_sql_prompt_no_previous_error_omits_block() -> None:
    prompt = build_sql_prompt(
        question="x",
        dialect="sqlite",
        structured_map_yaml="",
        previous_error=None,
    )
    assert "previous_error" not in prompt
    assert "erro anterior" not in prompt.lower()


def test_sql_prompt_pt_br() -> None:
    prompt = build_sql_prompt(
        question="x", dialect="sqlite", structured_map_yaml="", previous_error=None
    )
    assert "tarefa" in prompt.lower() or "papel" in prompt.lower()


def test_sql_prompt_instructs_select_only() -> None:
    """Prompt-side hint about SELECT-only — sqlglot is the enforcer but
    asking nicely costs nothing."""
    prompt = build_sql_prompt(
        question="x", dialect="sqlite", structured_map_yaml="", previous_error=None
    )
    assert "SELECT" in prompt


def test_strip_sql_fences_no_fence() -> None:
    assert strip_sql_fences("SELECT 1") == "SELECT 1"


def test_strip_sql_fences_sql_label() -> None:
    raw = "```sql\nSELECT 1\n```"
    assert strip_sql_fences(raw) == "SELECT 1"


def test_strip_sql_fences_unlabeled() -> None:
    raw = "```\nSELECT 1\n```"
    assert strip_sql_fences(raw) == "SELECT 1"


def test_strip_sql_fences_with_trailing_semicolon() -> None:
    raw = "```sql\nSELECT 1;\n```"
    assert strip_sql_fences(raw).rstrip(";").strip() == "SELECT 1"
