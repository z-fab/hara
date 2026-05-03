"""Tests for the Synthesizer prompt builder."""

from __future__ import annotations

from hara.agent.prompts.synthesizer import build_synthesizer_prompt
from hara.agent.state import Message, SqlEvidence, TextEvidence


def test_synthesizer_prompt_includes_question() -> None:
    prompt = build_synthesizer_prompt(
        question="Quanto produziu MT?",
        history=[],
        evidence_pool=[],
        style="Responda em estilo formal.",
    )
    assert "Quanto produziu MT?" in prompt


def test_synthesizer_prompt_includes_evidence_pool_xml() -> None:
    pool = [
        SqlEvidence(evidence_id=1, source_table="producao", columns=["uf"], row=("MT",)),
        TextEvidence(evidence_id=2, file_id="manejo.pdf", content="Texto."),
    ]
    prompt = build_synthesizer_prompt(question="x", history=[], evidence_pool=pool, style="x")
    assert 'evidence_id="1"' in prompt
    assert 'evidence_id="2"' in prompt
    assert "producao" in prompt
    assert "manejo.pdf" in prompt


def test_synthesizer_prompt_includes_style() -> None:
    prompt = build_synthesizer_prompt(
        question="x", history=[], evidence_pool=[], style="Responda em pirês de tamanduá."
    )
    assert "pirês de tamanduá" in prompt


def test_synthesizer_prompt_instructs_marker_usage() -> None:
    """Spec §3: o prompt deve explicar como usar <ref:N>."""
    prompt = build_synthesizer_prompt(question="x", history=[], evidence_pool=[], style="x")
    assert "<ref:N>" in prompt or "<ref:" in prompt


def test_synthesizer_prompt_includes_history() -> None:
    history = [
        Message(role="user", content="Pergunta passada"),
        Message(role="assistant", content="Resposta passada"),
    ]
    prompt = build_synthesizer_prompt(question="x", history=history, evidence_pool=[], style="x")
    assert "Pergunta passada" in prompt
    assert "Resposta passada" in prompt


def test_synthesizer_prompt_pt_br() -> None:
    prompt = build_synthesizer_prompt(question="x", history=[], evidence_pool=[], style="x")
    assert "papel" in prompt.lower() or "tarefa" in prompt.lower()


def test_synthesizer_prompt_instructs_no_hallucination() -> None:
    prompt = build_synthesizer_prompt(question="x", history=[], evidence_pool=[], style="x")
    # Constraint principal — não pode inventar dados.
    lower = prompt.lower()
    assert "alucin" in lower or "evidênc" in lower or "evidenc" in lower


def test_synthesizer_prompt_empty_pool_explicit_message() -> None:
    """Quando o pool está vazio, o prompt sinaliza isso ao LLM (importante
    no caminho de reuse onde só accumulated_evidence está disponível)."""
    prompt = build_synthesizer_prompt(question="x", history=[], evidence_pool=[], style="x")
    assert "<evidence_pool/>" in prompt or "vazio" in prompt.lower()
