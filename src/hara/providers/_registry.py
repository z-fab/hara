"""Provider discovery: entry-points + programmatic registration.

Mirrors the pattern from connectors/_registry.py.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hara.providers.embeddings.base import EmbeddingsProvider
    from hara.providers.llm.base import LLMProvider


_llm_registry: dict[str, type[LLMProvider]] = {}
_embeddings_registry: dict[str, type[EmbeddingsProvider]] = {}


def register_llm_provider(name: str, cls: type[LLMProvider]) -> None:
    _llm_registry[name] = cls


def register_embeddings_provider(name: str, cls: type[EmbeddingsProvider]) -> None:
    _embeddings_registry[name] = cls


def resolve_llm_provider_class(name: str) -> type[LLMProvider]:
    if name in _llm_registry:
        return _llm_registry[name]
    cls = _load_from_entry_points("hara.providers.llm", name)
    if cls is None:
        raise KeyError(f"Unknown LLM provider {name!r}. Available: {sorted(list_llm_providers())}")
    return cls


def resolve_embeddings_provider_class(name: str) -> type[EmbeddingsProvider]:
    if name in _embeddings_registry:
        return _embeddings_registry[name]
    cls = _load_from_entry_points("hara.providers.embeddings", name)
    if cls is None:
        raise KeyError(
            f"Unknown embeddings provider {name!r}. "
            f"Available: {sorted(list_embeddings_providers())}"
        )
    return cls


def list_llm_providers() -> set[str]:
    return _list_all("hara.providers.llm", _llm_registry)


def list_embeddings_providers() -> set[str]:
    return _list_all("hara.providers.embeddings", _embeddings_registry)


def _load_from_entry_points(group: str, name: str) -> type | None:
    eps = entry_points(group=group)
    for ep in eps:
        if ep.name == name:
            try:
                return ep.load()
            except ImportError as e:
                raise ImportError(
                    f"Provider {name!r} requires extra dependency. Try: pip install hara[{name}]"
                ) from e
    return None


def _list_all(group: str, programmatic: dict[str, type]) -> set[str]:
    eps = entry_points(group=group)
    return set(programmatic.keys()) | {ep.name for ep in eps}
