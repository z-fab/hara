"""LangGraph build + routing logic for the agent pipeline.

The graph is parameterized over node *callables* (not classes) so tests
can inject fake nodes without touching connectors/LLMs. The orchestrator
(orchestrator.py) wires real nodes via functools.partial.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph  # pyright: ignore[reportMissingTypeStubs]

from hara.agent.state import AgentState

NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class GraphConfig:
    """Per-build choices that affect topology."""

    verifier_mode: Literal["off", "signal"]


def route_after_planner(state: AgentState) -> list[str]:
    """Decide where to go after the Planner.

    - Empty routes → reuse implícito → straight to synthesizer.
    - Otherwise fan-out to whatever subquery types are present.

    Returns a list of node names; LangGraph handles parallel execution
    when len > 1.
    """
    routes: set[str] = state.get("routes_taken") or set()
    if not routes:
        return ["synthesizer"]
    out: list[str] = []
    if "sql" in routes:
        out.append("sql_executor")
    if "text" in routes:
        out.append("text_retriever")
    return out


def route_after_synthesizer(state: AgentState, cfg: GraphConfig) -> str:
    """Decide whether to verify or end."""
    _ = state  # routing here is purely config-driven, but keep the signature
    if cfg.verifier_mode == "signal":
        return "verifier"
    return "END"


def build_graph(
    cfg: GraphConfig,
    *,
    planner: NodeFn,
    sql_executor: NodeFn,
    text_retriever: NodeFn,
    synthesizer: NodeFn,
    verifier: NodeFn,
) -> Any:
    """Compile and return the agent graph.

    All nodes must already be partial-applied with their dependencies
    (LLMs, connectors, configs) — this builder is purely structural.
    """
    g = StateGraph(AgentState)

    g.add_node("planner", planner)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    g.add_node("sql_executor", sql_executor)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    g.add_node("text_retriever", text_retriever)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    g.add_node("synthesizer", synthesizer)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]
    g.add_node("verifier", verifier)  # pyright: ignore[reportUnknownMemberType, reportArgumentType]

    g.add_edge(START, "planner")

    g.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "sql_executor": "sql_executor",
            "text_retriever": "text_retriever",
            "synthesizer": "synthesizer",
        },
    )

    # Both retrieval nodes feed into synthesizer (LangGraph fan-in).
    g.add_edge("sql_executor", "synthesizer")
    g.add_edge("text_retriever", "synthesizer")

    def _post_synth(s: AgentState) -> str:
        return route_after_synthesizer(s, cfg)

    g.add_conditional_edges(
        "synthesizer",
        _post_synth,
        {"verifier": "verifier", "END": END},
    )

    g.add_edge("verifier", END)

    return g.compile()  # pyright: ignore[reportUnknownMemberType]
