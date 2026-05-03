"""Tests for the LangGraph build + routing logic.

We test the build (topology) and the routing functions (pure logic) here.
End-to-end execution via FakeChatModel is in integration tests (Task 24).
"""

from __future__ import annotations

from hara.agent.graph import (
    GraphConfig,
    build_graph,
    route_after_planner,
    route_after_synthesizer,
)
from hara.agent.state import AgentState


def _state(routes_taken: set[str]) -> AgentState:
    return {
        "question": "x",
        "thread_id": "t",
        "turn_id": "tr",
        "history": [],
        "accumulated_evidence": [],
        "subqueries": [],
        "routes_taken": routes_taken,
    }


def test_route_after_planner_reuse() -> None:
    """Empty routes_taken → reuse path → straight to synthesizer."""
    assert route_after_planner(_state(set())) == ["synthesizer"]


def test_route_after_planner_sql_only() -> None:
    assert route_after_planner(_state({"sql"})) == ["sql_executor"]


def test_route_after_planner_text_only() -> None:
    assert route_after_planner(_state({"text"})) == ["text_retriever"]


def test_route_after_planner_both() -> None:
    """Fan-out for hybrid queries."""
    routes = route_after_planner(_state({"sql", "text"}))
    assert set(routes) == {"sql_executor", "text_retriever"}


def test_route_after_synthesizer_off_goes_to_end() -> None:
    cfg = GraphConfig(verifier_mode="off")
    assert route_after_synthesizer(_state(set()), cfg) == "END"


def test_route_after_synthesizer_signal_goes_to_verifier() -> None:
    cfg = GraphConfig(verifier_mode="signal")
    assert route_after_synthesizer(_state(set()), cfg) == "verifier"


def test_build_graph_returns_compiled() -> None:
    """Smoke: graph compiles without raising; nodes exist."""
    cfg = GraphConfig(verifier_mode="off")

    async def _fake_node(_s: AgentState) -> dict:
        return {}

    graph = build_graph(
        cfg,
        planner=_fake_node,
        sql_executor=_fake_node,
        text_retriever=_fake_node,
        synthesizer=_fake_node,
        verifier=_fake_node,
    )
    # CompiledGraph exposes .nodes (dict)
    assert "planner" in graph.nodes
    assert "synthesizer" in graph.nodes


def test_build_graph_signal_mode_includes_verifier() -> None:
    cfg = GraphConfig(verifier_mode="signal")

    async def _fake(_s: AgentState) -> dict:
        return {}

    graph = build_graph(
        cfg,
        planner=_fake,
        sql_executor=_fake,
        text_retriever=_fake,
        synthesizer=_fake,
        verifier=_fake,
    )
    assert "verifier" in graph.nodes
