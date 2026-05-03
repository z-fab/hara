"""Tests for the Verifier prompt builder + schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hara.agent.prompts.verifier import VERIFIER_JSON_HINT, build_verifier_prompt
from hara.agent.state import SqlEvidence, TextEvidence, VerifierSignal


def test_verifier_signal_validates_pct_range() -> None:
    """pct_supported must be 0..1."""
    with pytest.raises(ValidationError):
        VerifierSignal(
            overall_pass=True,
            pct_supported=1.5,
            n_missing_aspects=0,
            weak_sentences=[],
        )


def test_verifier_prompt_includes_question_and_answer() -> None:
    prompt = build_verifier_prompt(
        question="Quanto produziu MT?",
        answer_text="MT produziu 100t <ref:1>.",
        evidence_pool=[
            SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,)),
        ],
    )
    assert "Quanto produziu MT?" in prompt
    assert "MT produziu 100t" in prompt


def test_verifier_prompt_includes_evidence() -> None:
    pool = [TextEvidence(evidence_id=1, file_id="manejo.pdf", content="Texto.")]
    prompt = build_verifier_prompt(question="x", answer_text="y <ref:1>", evidence_pool=pool)
    assert "manejo.pdf" in prompt
    assert "Texto." in prompt


def test_verifier_prompt_pt_br() -> None:
    prompt = build_verifier_prompt(question="x", answer_text="y", evidence_pool=[])
    assert "papel" in prompt.lower() or "tarefa" in prompt.lower()


def test_verifier_prompt_instructs_signal_fields() -> None:
    """Output must list the 4 fields: overall_pass, pct_supported,
    n_missing_aspects, weak_sentences."""
    prompt = build_verifier_prompt(question="x", answer_text="y", evidence_pool=[])
    assert "overall_pass" in prompt
    assert "pct_supported" in prompt
    assert "n_missing_aspects" in prompt
    assert "weak_sentences" in prompt


def test_verifier_json_hint_specifies_schema() -> None:
    assert "overall_pass" in VERIFIER_JSON_HINT
    assert "pct_supported" in VERIFIER_JSON_HINT
