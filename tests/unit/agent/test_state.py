"""Tests for the agent state types — pure dataclass-style validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hara.agent.state import (
    AgentState,
    Evidence,
    Message,
    SqlEvidence,
    SubQuery,
    TextEvidence,
    TokenUsage,
    VerifierSignal,
)


def test_subquery_validates_type() -> None:
    sq = SubQuery(id="sq_1", type="sql", question="Quanto produziu?")
    assert sq.type == "sql"


def test_subquery_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        SubQuery(id="sq_1", type="other", question="x")  # pyright: ignore[reportArgumentType]


def test_message_role_validates() -> None:
    m = Message(role="user", content="oi")
    assert m.role == "user"


def test_sql_evidence_carries_table_and_row() -> None:
    e = SqlEvidence(
        evidence_id=1,
        source_table="producao",
        columns=["uf", "tons"],
        row=("MT", 100),
    )
    assert e.evidence_id == 1
    assert e.source_table == "producao"


def test_text_evidence_carries_file_and_chunk() -> None:
    e = TextEvidence(
        evidence_id=2,
        file_id="manejo.pdf",
        content="Texto exemplar.",
        section="2.1",
    )
    assert e.file_id == "manejo.pdf"


def test_evidence_union_accepts_both() -> None:
    items: list[Evidence] = [
        SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,)),
        TextEvidence(evidence_id=2, file_id="f", content="x"),
    ]
    assert len(items) == 2


def test_verifier_signal_holds_four_fields() -> None:
    v = VerifierSignal(
        overall_pass=True,
        pct_supported=0.9,
        n_missing_aspects=0,
        weak_sentences=[],
    )
    assert v.overall_pass is True


def test_token_usage_defaults_zero() -> None:
    tu = TokenUsage()
    assert tu.input == 0
    assert tu.output == 0
    assert tu.total == 0


def test_agent_state_is_typed_dict() -> None:
    """Ensure AgentState behaves like a dict (TypedDict). Smoke check."""
    state: AgentState = {
        "question": "oi",
        "thread_id": "thr_1",
        "turn_id": "tr_1",
        "history": [],
        "accumulated_evidence": [],
        "subqueries": [],
        "routes_taken": set(),
        "sql_results": [],
        "text_results": [],
        "answer_text": "",
        "evidence_pool": [],
        "verifier_signal": None,
        "tokens": TokenUsage(),
        "latency_per_node": {},
    }
    assert state["question"] == "oi"
