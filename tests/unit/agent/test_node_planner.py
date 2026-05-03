"""Tests for the Planner node."""

from __future__ import annotations

from typing import Any, cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from hara.agent.nodes.planner import planner_node
from hara.agent.prompts.planner import PlannerOutput
from hara.agent.state import AgentState, Message, SqlEvidence, SubQuery


class _StructuredFakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def with_structured_output(self, _schema: type, **_kw: Any) -> Any:
        outer = self

        class _R:
            async def ainvoke(self, _i: Any) -> Any:
                r = outer._responses[outer._idx]
                outer._idx += 1
                if isinstance(r, Exception):
                    raise r
                return r

        return _R()


class _PromptOnlyFakeLLM:
    def __init__(self, text_responses: list[Any]) -> None:
        self._responses = list(text_responses)
        self._idx = 0

    def with_structured_output(self, _schema: type, **_kw: Any) -> Any:
        raise NotImplementedError("fake")

    async def ainvoke(self, _i: Any) -> Any:
        r = self._responses[self._idx]
        self._idx += 1
        if isinstance(r, Exception):
            raise r
        return AIMessage(content=r) if isinstance(r, str) else r


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


def _state(question: str = "Quanto produziu MT?", **extra: Any) -> AgentState:
    base: AgentState = {
        "question": question,
        "thread_id": "thr_1",
        "turn_id": "tr_1",
        "history": [],
        "accumulated_evidence": [],
    }
    base.update(extra)  # type: ignore[typeddict-item]
    return base


async def test_planner_structured_output_path() -> None:
    fake = _StructuredFakeLLM(
        [
            PlannerOutput(
                subqueries=[
                    SubQuery(id="sq_1", type="sql", question="produção MT?"),
                ]
            )
        ]
    )
    delta = await planner_node(
        _state(),
        llm=_llm(fake),
        structured_map_yaml="tables: []",
        unstructured_map_yaml="documents: []",
    )
    assert "subqueries" in delta
    assert len(delta["subqueries"]) == 1
    assert delta["subqueries"][0].type == "sql"
    assert "routes_taken" in delta
    assert delta["routes_taken"] == {"sql"}


async def test_planner_prompt_only_path_with_json() -> None:
    fake = _PromptOnlyFakeLLM(
        [
            '{"subqueries": [{"id": "sq_1", "type": "text", '
            '"question": "manejo?", "sources": ["manejo.pdf"]}]}'
        ]
    )
    delta = await planner_node(
        _state(),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="documents: []",
    )
    assert delta["subqueries"][0].type == "text"
    assert delta["routes_taken"] == {"text"}


async def test_planner_prompt_only_with_markdown_fence() -> None:
    fake = _PromptOnlyFakeLLM(
        ['```json\n{"subqueries": [{"id": "sq_1", "type": "sql", "question": "x"}]}\n```']
    )
    delta = await planner_node(
        _state(),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="",
    )
    assert delta["subqueries"][0].type == "sql"


async def test_planner_empty_subqueries_signals_reuse() -> None:
    """Lista vazia significa reuse — routes_taken vai vazio."""
    fake = _StructuredFakeLLM([PlannerOutput(subqueries=[])])
    delta = await planner_node(
        _state(),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="",
    )
    assert delta["subqueries"] == []
    assert delta["routes_taken"] == set()


async def test_planner_includes_history_in_prompt() -> None:
    """The Planner sees prior turns. Sanity check structured output path runs."""
    fake = _StructuredFakeLLM([PlannerOutput(subqueries=[])])
    history = [
        Message(role="user", content="prior question"),
        Message(role="assistant", content="prior answer"),
    ]
    delta = await planner_node(
        _state(history=history),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="",
    )
    assert delta["subqueries"] == []


async def test_planner_dual_failure_raises() -> None:
    """Both structured output AND prompt-only fallback fail → raises."""

    class _BothFail:
        def with_structured_output(self, _s: type, **_k: Any) -> Any:
            raise NotImplementedError

        async def ainvoke(self, _i: Any) -> Any:
            return AIMessage(content="garbage that's not JSON")

    with pytest.raises(RuntimeError, match="planner"):
        await planner_node(
            _state(),
            llm=_llm(_BothFail()),
            structured_map_yaml="",
            unstructured_map_yaml="",
        )


async def test_planner_records_tracking() -> None:
    fake = _StructuredFakeLLM([PlannerOutput(subqueries=[])])
    delta = await planner_node(
        _state(),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="",
    )
    assert "tokens" in delta
    assert "latency_per_node" in delta
    assert "planner" in delta["latency_per_node"]
    assert delta["latency_per_node"]["planner"] >= 0.0


async def test_planner_emits_empty_subqueries_when_accumulated_sufficient() -> None:
    """Spec §3 reuse implícito — when accumulated_evidence already answers,
    Planner returns subqueries=[]. We trust the LLM here; this test confirms
    the node propagates routes_taken=set() correctly so the graph routes
    directly to the Synthesizer (skipping retrieval)."""
    fake = _StructuredFakeLLM([PlannerOutput(subqueries=[])])
    accumulated = [SqlEvidence(evidence_id=1, source_table="producao", columns=["uf"], row=("MT",))]
    delta = await planner_node(
        _state(question="follow-up", accumulated_evidence=accumulated),
        llm=_llm(fake),
        structured_map_yaml="",
        unstructured_map_yaml="",
    )
    assert delta["subqueries"] == []
    assert delta["routes_taken"] == set()
