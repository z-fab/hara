"""Connector discovery: entry-points + programmatic registration.

Discovery order on first lookup:
    1. Programmatically registered classes (in-process registry)
    2. Entry-points declared in installed packages (pyproject.toml)

The agent and `hara doctor` use `resolve_*_connector_class()` to fetch
the implementation, then call `from_config()` to instantiate.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hara.connectors.sql.base import SQLConnector
    from hara.connectors.vector.base import VectorConnector


_sql_registry: dict[str, type[SQLConnector]] = {}
_vector_registry: dict[str, type[VectorConnector]] = {}


def register_sql_connector(name: str, cls: type[SQLConnector]) -> None:
    """Register a SQL connector class under `name`. Overrides any existing."""
    _sql_registry[name] = cls


def register_vector_connector(name: str, cls: type[VectorConnector]) -> None:
    """Register a Vector connector class under `name`. Overrides any existing."""
    _vector_registry[name] = cls


def resolve_sql_connector_class(name: str) -> type[SQLConnector]:
    """Look up a SQL connector by name. Programmatic registry wins over entry-points."""
    if name in _sql_registry:
        return _sql_registry[name]
    cls = _load_from_entry_points("hara.connectors.sql", name)
    if cls is None:
        raise KeyError(
            f"Unknown SQL connector {name!r}. Available: {sorted(list_sql_connectors())}"
        )
    return cls


def resolve_vector_connector_class(name: str) -> type[VectorConnector]:
    """Look up a Vector connector by name."""
    if name in _vector_registry:
        return _vector_registry[name]
    cls = _load_from_entry_points("hara.connectors.vector", name)
    if cls is None:
        raise KeyError(
            f"Unknown vector connector {name!r}. Available: {sorted(list_vector_connectors())}"
        )
    return cls


def list_sql_connectors() -> set[str]:
    return _list_all("hara.connectors.sql", _sql_registry)


def list_vector_connectors() -> set[str]:
    return _list_all("hara.connectors.vector", _vector_registry)


def _load_from_entry_points(group: str, name: str) -> type | None:
    """Load a class declared as entry-point. Returns None if not found.

    Wrapped in a try/except so missing optional deps (e.g. chromadb not
    installed) yield a helpful error rather than a cryptic ImportError.
    """
    eps = entry_points(group=group)
    for ep in eps:
        if ep.name == name:
            try:
                return ep.load()
            except ImportError as e:
                raise ImportError(
                    f"Connector {name!r} requires extra dependency. Try: pip install hara[{name}]"
                ) from e
    return None


def _list_all(group: str, programmatic: dict[str, type]) -> set[str]:
    eps = entry_points(group=group)
    return set(programmatic.keys()) | {ep.name for ep in eps}
