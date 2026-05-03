"""Dual-mode LLM invocation: with_structured_output (preferred) + prompt-only JSON fallback.

Originally lived in services/semantic_map.py; promoted to a shared utility
so every agent node can use the same pattern without an import cycle
(prompts -> services would be wrong; agent/llm_invoke is neutral).

Behavior is identical to the semantic_map version - the docstring there
remains the canonical explanation.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ValidationError

from hara.utils.llm_parsing import parse_llm_json

T = TypeVar("T", bound=BaseModel)
log = logging.getLogger(__name__)


async def invoke_with_fallback(
    llm: BaseChatModel,
    *,
    prompt: str,
    json_hint: str,
    schema: type[T],
    identity: str,
) -> T | None:
    """Try ``llm.with_structured_output(schema)`` first; on failure, fall back
    to prompt-only JSON parsed via parse_llm_json + validated against schema.

    Returns the validated instance or None on dual failure. Caller treats
    None as a soft failure (logs, recovers, retries with new prompt, etc.).
    """
    # Path A: structured output
    try:
        structured_llm = llm.with_structured_output(schema)
        result = await structured_llm.ainvoke(prompt)
        if isinstance(result, schema):
            return result
        log.warning(
            "structured-output for %s returned wrong type %s; falling back",
            identity,
            type(result).__name__,
        )
    except NotImplementedError as e:
        log.info("structured-output not supported for %s: %s; using prompt-only", identity, e)
    except Exception as e:
        log.warning("structured-output failed for %s: %s; falling back", identity, e)

    # Path B: prompt-only JSON fallback
    try:
        msg = await llm.ainvoke(prompt + json_hint)
    except Exception as e:
        log.warning("LLM also failed in fallback for %s: %s", identity, e)
        return None

    text = extract_text(msg)
    parsed = parse_llm_json(text)
    if parsed is None:
        return None
    if not isinstance(parsed, dict):
        log.warning(
            "fallback parse produced non-dict for %s (got %s); raw (truncated): %s",
            identity,
            type(parsed).__name__,
            text[:300],
        )
        return None
    try:
        return schema.model_validate(parsed)
    except ValidationError as e:
        log.warning(
            "schema validation failed for %s: %s; raw (truncated): %s",
            identity,
            e,
            text[:300],
        )
        return None


def extract_text(msg: Any) -> str:
    """Normalize ``BaseMessage.content`` (or a chunk) to a plain string.

    OpenAI/Anthropic return ``content`` as ``str``. Gemini (and other
    providers using LangChain's typed-content blocks) return a list of
    dicts shaped like ``[{"type": "text", "text": "...", "extras": {...}}]``.
    Hardened against:

    - ``str`` (pass through);
    - ``list[dict | str]`` (flatten to ``"".join(text_parts)``);
    - anything else (``str(value)`` as last resort).

    Used by every node that pulls text from an LLM response. Mirrors
    ``/experimentos`` ``utils/tracking.get_text_content``.
    """
    if isinstance(msg, str):
        return msg
    content = getattr(msg, "content", msg)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:  # pyright: ignore[reportUnknownVariableType]
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # Type-tagged content block; only "text" carries useful payload
                # for our parsers. Tool-use / signature blocks are ignored.
                text = block.get("text", "")  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)
