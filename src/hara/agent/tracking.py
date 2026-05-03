"""Token-usage extraction (provider-agnostic) + per-node latency timer.

LangChain's AIMessage carries usage in two places depending on provider:
- `usage_metadata` (newer; OpenAI, Anthropic via langchain-anthropic 0.2+).
- `response_metadata.token_usage` (older; some Google/OpenRouter shapes).

We probe both before giving up and returning zeros — never raises.
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

from hara.agent.state import TokenUsage


def _coerce_int(value: Any) -> int:
    """Tolerate None / missing / non-numeric — returns 0 if cast fails."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def extract_token_usage(msg: Any) -> TokenUsage:
    """Best-effort token usage extraction from an LLM response.

    Returns zeros if the response shape doesn't match either known pattern
    (e.g. `with_structured_output` returned a plain Pydantic model). The
    caller should still record latency.
    """
    # Path A: usage_metadata (LangChain Core >=0.2 standard)
    um = getattr(msg, "usage_metadata", None)
    if isinstance(um, dict):
        um_d = cast(dict[str, Any], um)
        return TokenUsage(
            input=_coerce_int(um_d.get("input_tokens")),
            output=_coerce_int(um_d.get("output_tokens")),
            total=_coerce_int(um_d.get("total_tokens")),
        )

    # Path B: response_metadata.token_usage
    rm = getattr(msg, "response_metadata", None)
    if isinstance(rm, dict):
        rm_d = cast(dict[str, Any], rm)
        tu = rm_d.get("token_usage")
        if isinstance(tu, dict):
            tu_d = cast(dict[str, Any], tu)
            return TokenUsage(
                input=_coerce_int(tu_d.get("prompt_tokens")),
                output=_coerce_int(tu_d.get("completion_tokens")),
                total=_coerce_int(tu_d.get("total_tokens")),
            )

    return TokenUsage()


@dataclass
class _Timer:
    start: float
    elapsed: float = 0.0


@contextmanager
def measure_node_latency() -> Generator[_Timer, None, None]:
    """Context manager that records ``elapsed`` even if the body raises.

    Used by every node — wraps the LLM call + connector call. Returns
    a small object so the node body can read `timer.elapsed` after exit.
    """
    timer = _Timer(start=time.perf_counter())
    try:
        yield timer
    finally:
        timer.elapsed = time.perf_counter() - timer.start
