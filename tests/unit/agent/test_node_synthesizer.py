"""Tests for the Synthesizer node — streamed text + evidence pool emit."""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from hara.agent.nodes.synthesizer import synthesizer_node
from hara.agent.state import AgentState, SqlEvidence, SubQuery, TextEvidence


class _StreamFakeLLM:
    """Fake LLM whose astream() yields chunks, ainvoke() returns concatenation."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = list(chunks)

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        raise NotImplementedError("synthesizer doesn't use structured output")

    async def astream(self, _i: Any) -> Any:
        for c in self._chunks:
            yield AIMessageChunk(content=c)

    async def ainvoke(self, _i: Any) -> AIMessage:
        return AIMessage(content="".join(self._chunks))


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


def _state(
    sql_results: list[dict[str, Any]] | None = None,
    text_results: list[dict[str, Any]] | None = None,
    accumulated_evidence: list | None = None,
) -> AgentState:
    return {
        "question": "Quanto produziu MT?",
        "thread_id": "t",
        "turn_id": "tr",
        "history": [],
        "accumulated_evidence": accumulated_evidence or [],
        "subqueries": [SubQuery(id="sq_1", type="sql", question="x")],
        "routes_taken": {"sql"},
        "sql_results": sql_results or [],
        "text_results": text_results or [],
    }


async def test_synthesizer_streams_and_concatenates() -> None:
    fake = _StreamFakeLLM(["MT produziu ", "100t ", "<ref:1>."])
    state = _state(
        sql_results=[
            {
                "task_query": "x",
                "sql_query": "SELECT 1",
                "tables": ["producao"],
                "columns": ["uf", "tons"],
                "rows": [("MT", 100)],
            }
        ]
    )
    delta = await synthesizer_node(state, llm=_llm(fake), style="x")
    assert delta["answer_text"] == "MT produziu 100t <ref:1>."


async def test_synthesizer_invokes_on_token_callback() -> None:
    """CLI uses on_token to drive Rich Live."""
    received: list[str] = []
    fake = _StreamFakeLLM(["A ", "B ", "C"])
    state = _state(
        sql_results=[
            {"task_query": "x", "sql_query": "x", "tables": ["t"], "columns": ["c"], "rows": [(1,)]}
        ]
    )
    delta = await synthesizer_node(state, llm=_llm(fake), style="x", on_token=received.append)
    assert received == ["A ", "B ", "C"]
    assert delta["answer_text"] == "A B C"


async def test_synthesizer_emits_evidence_pool() -> None:
    """The pool is built fresh and put in state for Verifier + orchestrator."""
    fake = _StreamFakeLLM(["x"])
    state = _state(
        sql_results=[
            {
                "task_query": "x",
                "sql_query": "x",
                "tables": ["producao"],
                "columns": ["uf"],
                "rows": [("MT",)],
            }
        ],
        text_results=[
            {
                "task_query": "y",
                "chunks": [{"file_id": "manejo.pdf", "content": "z", "metadata": {}}],
            }
        ],
    )
    delta = await synthesizer_node(state, llm=_llm(fake), style="x")
    pool = delta["evidence_pool"]
    assert len(pool) == 2
    assert isinstance(pool[0], SqlEvidence)
    assert isinstance(pool[1], TextEvidence)


async def test_synthesizer_uses_accumulated_when_no_results() -> None:
    """Reuse path: sql_results+text_results vazios, mas accumulated_evidence
    tem dados — pool deve ser exatamente accumulated_evidence."""
    accumulated = [SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,))]
    fake = _StreamFakeLLM(["uso turno passado <ref:1>."])
    state = _state(accumulated_evidence=accumulated)
    delta = await synthesizer_node(state, llm=_llm(fake), style="x")
    pool = delta["evidence_pool"]
    assert len(pool) == 1
    assert pool[0].evidence_id == 1


async def test_synthesizer_records_tracking() -> None:
    fake = _StreamFakeLLM(["x"])
    state = _state()
    delta = await synthesizer_node(state, llm=_llm(fake), style="x")
    assert "latency_per_node" in delta
    assert "synthesizer" in delta["latency_per_node"]


async def test_synthesizer_merges_accumulated_with_new_results() -> None:
    """Codex review #2 P2: when there's BOTH accumulated_evidence AND new
    retrievals, the pool must include both, with new IDs continuing past
    the max accumulated id (so prior <ref:N> markers in history don't
    collide with new ones).
    """
    accumulated = [
        SqlEvidence(evidence_id=1, source_table="prior", columns=["c"], row=("a",)),
        SqlEvidence(evidence_id=2, source_table="prior", columns=["c"], row=("b",)),
    ]
    fake = _StreamFakeLLM(["x"])
    state = _state(
        sql_results=[
            {
                "task_query": "new",
                "sql_query": "SELECT 1",
                "tables": ["producao"],
                "columns": ["uf"],
                "rows": [("MT",)],
            }
        ],
        accumulated_evidence=accumulated,
    )
    delta = await synthesizer_node(state, llm=_llm(fake), style="x")
    pool = delta["evidence_pool"]
    # 2 accumulated + 1 new
    assert len(pool) == 3
    assert [e.evidence_id for e in pool] == [1, 2, 3]
    # Sources match: first two are 'prior', last is 'producao'
    assert pool[0].source_table == "prior"
    assert pool[2].source_table == "producao"
