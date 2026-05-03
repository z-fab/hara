"""Robust JSON extraction from LLM text responses.

When ``llm.with_structured_output(Schema)`` is unavailable (older Ollama
models, some OpenRouter gateways, providers that don't expose JSON schema
mode), HARA falls back to prompt-only JSON. Models in this regime wrap
JSON in markdown fences, XML tags, or surrounding prose.

This module ports the parsing strategy from the dissertation experimentos
repo (``src/utils/tracking.py::parse_llm_json``): try plain JSON first,
then strip a markdown fence, then strip an XML wrapper, then walk the
text looking for the first balanced ``{...}`` / ``[...]`` block.

The same helper is intended to be reused by Plano 3 agent nodes (Planner,
SQL Executor, Verifier) which will face the same fallback need.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json|yaml|yml)?\s*\n(.*?)\n```", re.DOTALL | re.IGNORECASE)
# An outer wrapper like <output>...</output>, <json>...</json>, <result>...</result>.
# Tag name captured so the closing tag must match.
_XML_WRAPPER_RE = re.compile(r"^\s*<([\w-]+)>(.*)</\1>\s*$", re.DOTALL)


def parse_llm_json(text: str) -> Any | None:
    """Best-effort JSON extraction from an LLM response.

    Tries in order:
        1. Plain ``json.loads``
        2. Strip a single ```json/```yaml fence
        3. Strip an outer ``<tag>...</tag>`` XML wrapper
        4. Walk the text and parse the first balanced ``{...}`` or ``[...]``

    ``strict=False`` lets newlines and tabs appear inside JSON strings
    (LLMs do this routinely).

    Returns ``None`` and logs a warning if nothing parses.
    """
    cleaned = text.strip()

    # 1. Plain JSON
    try:
        return json.loads(cleaned, strict=False)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Strip markdown fence
    fence = _FENCE_RE.search(cleaned)
    if fence:
        try:
            return json.loads(fence.group(1), strict=False)
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. Strip XML wrapper
    xml = _XML_WRAPPER_RE.match(cleaned)
    if xml:
        inner = xml.group(2).strip()
        try:
            return json.loads(inner, strict=False)
        except (json.JSONDecodeError, ValueError):
            pass

    # 4. Balanced extraction
    extracted = _extract_first_balanced_block(cleaned)
    if extracted is not None:
        return extracted

    log.warning("failed to parse LLM JSON. raw (truncated): %s", text[:500])
    return None


def _extract_first_balanced_block(text: str) -> Any | None:
    """Find the first balanced ``{...}`` (or ``[...]``) in ``text`` and parse it.

    Handles nested objects and JSON strings that contain braces. Skips
    over backslash-escaped quotes inside strings so the depth counter
    isn't fooled.
    """
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        return json.loads(candidate, strict=False)
                    except (json.JSONDecodeError, ValueError):
                        break  # try the other delimiter
    return None
