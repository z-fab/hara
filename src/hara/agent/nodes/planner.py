"""Planner node — decompõe a pergunta em sub-queries tipadas.

Uses dual-mode invocation (structured_output preferred, prompt-only fallback).
Empty subqueries list = reuse implícito.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.llm_invoke import invoke_with_fallback
from hara.agent.prompts.planner import (
    PLANNER_JSON_HINT,
    PlannerOutput,
    build_planner_prompt,
)
from hara.agent.state import AgentState, TokenUsage
from hara.agent.tracking import measure_node_latency

log = logging.getLogger(__name__)


async def planner_node(
    state: AgentState,
    *,
    llm: BaseChatModel,
    structured_map_yaml: str,
    unstructured_map_yaml: str,
) -> dict[str, Any]:
    """Run the Planner. Returns a state delta to merge."""
    question = state.get("question", "")
    prompt = build_planner_prompt(
        question=question,
        history=state.get("history", []),
        accumulated_evidence=state.get("accumulated_evidence", []),
        structured_map_yaml=structured_map_yaml,
        unstructured_map_yaml=unstructured_map_yaml,
    )

    with measure_node_latency() as timer:
        out = await invoke_with_fallback(
            llm,
            prompt=prompt,
            json_hint=PLANNER_JSON_HINT,
            schema=PlannerOutput,
            identity="planner",
        )

    if out is None:
        raise RuntimeError(
            "planner failed: both structured-output and JSON fallback returned no valid output"
        )

    routes: set[str] = {sq.type for sq in out.subqueries}

    return {
        "subqueries": out.subqueries,
        "routes_taken": routes,
        "tokens": TokenUsage(),
        "latency_per_node": {"planner": timer.elapsed},
    }
