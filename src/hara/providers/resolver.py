"""Per-node model resolution.

Defaults are baked from the dissertation's findings:
    planner=hard    (decomposition / reasoning)
    verifier=hard   (semantic checking)
    sql=soft        (structured generation)
    synthesizer=soft (text generation from evidence)

Users can override any node by setting [models.<node>] in hara.toml.
"""

from __future__ import annotations

from typing import Literal

from hara.config.settings import ModelRef, ModelsSettings

NodeName = Literal["planner", "sql", "synthesizer", "verifier"]

_DEFAULT_TIER: dict[str, str] = {
    "planner": "hard",
    "verifier": "hard",
    "sql": "soft",
    "synthesizer": "soft",
}


def resolve_node_model_ref(node: str, settings: ModelsSettings) -> ModelRef:
    """Look up the (provider, model) ref for a given pipeline node.

    Resolution order:
        1. settings.<node> if set explicitly
        2. settings.hard or settings.soft based on the dissertation default

    Raises:
        ValueError: If `node` is not a known pipeline node.
    """
    if node not in _DEFAULT_TIER:
        raise ValueError(f"Unknown node {node!r}. Known: {sorted(_DEFAULT_TIER)}")

    explicit = getattr(settings, node, None)
    if explicit is not None:
        return explicit

    tier = _DEFAULT_TIER[node]
    return getattr(settings, tier)
