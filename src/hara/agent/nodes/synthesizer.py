"""Synthesizer node — streams text with `<ref:N>` markers.

The LLM is called via `astream()` so each chunk is emitted as it's produced.
Caller passes `on_token` to receive deltas (Rich Live in CLI; SSE in API
for Plano 4). Without it, the node still works — just buffers silently.

Evidence pool is built here (not in the orchestrator) so the Verifier
node sees the SAME pool the LLM saw, even if the orchestrator decided
to filter the pool further afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.evidence import build_evidence_pool
from hara.agent.llm_invoke import extract_text
from hara.agent.prompts.synthesizer import build_synthesizer_prompt
from hara.agent.state import AgentState, Evidence, TokenUsage
from hara.agent.tracking import measure_node_latency


async def synthesizer_node(
    state: AgentState,
    *,
    llm: BaseChatModel,
    style: str,
    on_token: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Stream LLM tokens; concatenate into `answer_text`. Build evidence_pool."""
    sql_results = state.get("sql_results", [])
    text_results = state.get("text_results", [])
    accumulated = state.get("accumulated_evidence", [])

    # Multi-turn merge: accumulated_evidence (carrying its original
    # evidence_ids from prior turns) plus the new retrievals. New evidence
    # IDs continue *after* the highest accumulated id so <ref:N> markers
    # the LLM emits cannot collide with markers already in `history`.
    pool: list[Evidence] = list(accumulated)
    if sql_results or text_results:
        next_id = max((e.evidence_id for e in pool), default=0) + 1
        pool.extend(
            build_evidence_pool(
                sql_results=sql_results,
                text_results=text_results,
                starting_id=next_id,
            )
        )

    prompt = build_synthesizer_prompt(
        question=state.get("question", ""),
        history=state.get("history", []),
        evidence_pool=pool,
        style=style,
    )

    buf: list[str] = []
    with measure_node_latency() as timer:
        async for chunk in llm.astream(prompt):
            # extract_text handles typed-content chunks from Gemini/Anthropic
            # as well as plain string chunks from OpenAI.
            content = extract_text(chunk)
            if not content:
                continue
            buf.append(content)
            if on_token is not None:
                on_token(content)

    return {
        "answer_text": "".join(buf),
        "evidence_pool": pool,
        "tokens": TokenUsage(),  # streaming responses rarely carry usage in chunks
        "latency_per_node": {"synthesizer": timer.elapsed},
    }
