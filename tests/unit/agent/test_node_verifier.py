"""Tests for the Verifier node (signal-only)."""

from __future__ import annotations

from typing import Any, cast

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from hara.agent.nodes.verifier import verifier_node
from hara.agent.state import AgentState, SqlEvidence, VerifierSignal


class _StructuredFakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._r = list(responses)
        self._i = 0

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        outer = self

        class _R:
            async def ainvoke(self, _i: Any) -> Any:
                r = outer._r[outer._i]
                outer._i += 1
                if isinstance(r, Exception):
                    raise r
                return r

        return _R()


class _PromptOnlyFakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self._r = list(responses)
        self._i = 0

    def with_structured_output(self, _s: type, **_k: Any) -> Any:
        raise NotImplementedError

    async def ainvoke(self, _i: Any) -> Any:
        r = self._r[self._i]
        self._i += 1
        return AIMessage(content=r) if isinstance(r, str) else r


def _llm(f: object) -> BaseChatModel:
    return cast(BaseChatModel, f)


def _state() -> AgentState:
    return {
        "question": "x",
        "thread_id": "t",
        "turn_id": "tr",
        "history": [],
        "accumulated_evidence": [],
        "subqueries": [],
        "routes_taken": set(),
        "sql_results": [],
        "text_results": [],
        "answer_text": "MT produziu 100t <ref:1>.",
        "evidence_pool": [SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,))],
    }


async def test_verifier_structured_output_path() -> None:
    fake = _StructuredFakeLLM(
        [
            VerifierSignal(
                overall_pass=True,
                pct_supported=0.95,
                n_missing_aspects=0,
                weak_sentences=[],
            )
        ]
    )
    delta = await verifier_node(_state(), llm=_llm(fake))
    assert delta["verifier_signal"].overall_pass is True
    assert delta["verifier_signal"].pct_supported == 0.95


async def test_verifier_prompt_only_path() -> None:
    fake = _PromptOnlyFakeLLM(
        [
            '{"overall_pass": false, "pct_supported": 0.4, '
            '"n_missing_aspects": 2, "weak_sentences": ["x"]}'
        ]
    )
    delta = await verifier_node(_state(), llm=_llm(fake))
    assert delta["verifier_signal"].overall_pass is False
    assert delta["verifier_signal"].n_missing_aspects == 2


async def test_verifier_failure_returns_none_not_raises() -> None:
    """Verifier never blocks the response. Failure → signal=None, log warn."""
    fake = _PromptOnlyFakeLLM(["garbage that's not JSON"])
    delta = await verifier_node(_state(), llm=_llm(fake))
    assert delta["verifier_signal"] is None


async def test_verifier_records_tracking() -> None:
    fake = _StructuredFakeLLM(
        [
            VerifierSignal(
                overall_pass=True, pct_supported=1.0, n_missing_aspects=0, weak_sentences=[]
            )
        ]
    )
    delta = await verifier_node(_state(), llm=_llm(fake))
    assert "verifier" in delta["latency_per_node"]
