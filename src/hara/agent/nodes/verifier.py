"""Verifier node — signal-only quality scoring.

Spec §3 Verifier modes: only runs when [verifier].mode == "signal" (caller's
decision). 1 LLM call. Failure → verifier_signal=None; never blocks.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.llm_invoke import invoke_with_fallback
from hara.agent.prompts.verifier import (
    VERIFIER_JSON_HINT,
    VerifierSignal,
    build_verifier_prompt,
)
from hara.agent.state import AgentState
from hara.agent.tracking import measure_node_latency

log = logging.getLogger(__name__)


async def verifier_node(
    state: AgentState,
    *,
    llm: BaseChatModel,
) -> dict[str, Any]:
    """Run Verifier signal-only. Return state delta with verifier_signal (or None)."""
    prompt = build_verifier_prompt(
        question=state.get("question", ""),
        answer_text=state.get("answer_text", ""),
        evidence_pool=state.get("evidence_pool", []),
    )

    with measure_node_latency() as timer:
        signal = await invoke_with_fallback(
            llm,
            prompt=prompt,
            json_hint=VERIFIER_JSON_HINT,
            schema=VerifierSignal,
            identity="verifier",
        )

    if signal is None:
        log.warning("verifier returned no usable signal; metadata.verifier will be null")

    return {
        "verifier_signal": signal,
        "latency_per_node": {"verifier": timer.elapsed},
    }
