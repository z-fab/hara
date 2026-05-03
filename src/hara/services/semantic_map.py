"""Builds the YAML-friendly dicts behind structured.yaml / unstructured.yaml.

Two entry points:

- :func:`build_structured_map` - describes SQL tables.
- :func:`build_unstructured_map` - describes vector-indexed documents.

Each call goes through a two-stage LLM strategy:

1. **Preferred path — structured output:** call ``llm.with_structured_output(Schema)``
   so the provider enforces the output shape natively (OpenAI JSON Schema,
   Anthropic tool use, Google function calling). When this works, parsing
   is impossible to get wrong.

2. **Fallback path — prompt-only JSON:** older Ollama models, some OpenRouter
   gateways, and unconfigured providers don't support structured output. We
   append a JSON hint to the prompt, parse the response with
   :func:`hara.utils.llm_parsing.parse_llm_json` (which survives markdown
   fences, XML wrappers, and prose-embedded blocks), then validate against
   the same Pydantic schema. This mirrors how the dissertation experimentos
   handled it before structured-output APIs were widely available.

The dict layer (returned by these builders) is what gets dumped to YAML;
column statistics are merged in here from the connector's ``TableInfo``,
not from the LLM.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from langchain_core.language_models.chat_models import BaseChatModel

from hara.agent.llm_invoke import invoke_with_fallback as _invoke_with_fallback
from hara.agent.prompts.semantic_map import (
    STRUCTURED_JSON_HINT,
    UNSTRUCTURED_JSON_HINT,
    DocumentDescription,
    TableDescription,
    structured_map_prompt,
    unstructured_map_prompt,
)
from hara.connectors.sql.base import ColumnStatistics, SQLConnector, TableInfo
from hara.connectors.vector.base import VectorConnector
from hara.services.session_store import SessionStore

log = logging.getLogger(__name__)

# Tables created by HARA itself - never described in the structured map.
_HARA_INTERNAL_TABLES = {
    "hara_threads",
    "hara_turns",
    "hara_events",
    "hara_ingested_files",
}


@dataclass
class MapBuildResult:
    """Outcome of one semantic-map builder pass.

    ``data`` is the dict ready to dump as YAML (``{"tables": [...]}`` or
    ``{"documents": [...]}``). ``failed_ids`` collects identifiers (table
    name or ``file_id``) that the LLM/YAML pipeline failed on. The CLI
    only saves the state-lock when ``failed_ids`` is empty — otherwise
    the next non-``--force`` run would skip retrying the items that were
    silently dropped from the map.
    """

    data: dict[str, Any] = field(default_factory=dict[str, Any])
    failed_ids: list[str] = field(default_factory=list[str])


async def build_structured_map(connector: SQLConnector, llm: BaseChatModel) -> MapBuildResult:
    """Return a :class:`MapBuildResult` whose ``data`` is
    ``{"tables": [...]}`` ready to dump as ``structured.yaml``.

    Filters out HARA's own session tables so they never end up exposed to
    the Planner. Tables whose LLM/YAML step failed are dropped from
    ``data`` and recorded in ``failed_ids``.
    """
    tables = [t for t in await connector.list_tables() if t.name not in _HARA_INTERNAL_TABLES]
    entries: list[dict[str, Any]] = []
    failed: list[str] = []
    for tbl in tables:
        entry = await _describe_table(tbl, llm)
        if entry is not None:
            entries.append(entry)
        else:
            failed.append(tbl.name)
    return MapBuildResult(data={"tables": entries}, failed_ids=failed)


async def _describe_table(info: TableInfo, llm: BaseChatModel) -> dict[str, Any] | None:
    prompt = structured_map_prompt(info)
    description = await _invoke_with_fallback(
        llm,
        prompt=prompt,
        json_hint=STRUCTURED_JSON_HINT,
        schema=TableDescription,
        identity=f"table {info.name}",
    )
    if description is None:
        return None
    return _table_description_to_dict(description, info)


def _table_description_to_dict(description: TableDescription, info: TableInfo) -> dict[str, Any]:
    declared_cols = {c.name for c in info.columns}
    columns_out: list[dict[str, Any]] = []
    for col in description.columns:
        if col.name not in declared_cols:
            continue  # drop hallucinated columns
        col_dict: dict[str, Any] = {
            "name": col.name,
            "type": col.type or _lookup_type(info, col.name),
            "description": col.description.strip(),
        }
        # Statistics come from the connector (source of truth), not from the
        # LLM - the YAML always carries true numbers even if the model omits
        # them. Only emit non-empty stats blocks.
        stats = (info.column_statistics or {}).get(col.name)
        stats_dict = _stats_to_dict(stats)
        if stats_dict:
            col_dict["statistics"] = stats_dict
        columns_out.append(col_dict)

    return {
        "table_name": info.name,
        "description": description.description.strip(),
        "columns": columns_out,
        "row_count": info.row_count,
    }


def _stats_to_dict(s: ColumnStatistics | None) -> dict[str, Any]:
    if s is None:
        return {}
    out: dict[str, Any] = {}
    if s.row_count is not None:
        out["row_count"] = s.row_count
    if s.null_percentage is not None:
        out["null_percentage"] = f"{s.null_percentage}%"  # match /experimentos string format
    if s.min is not None:
        out["min"] = s.min
    if s.max is not None:
        out["max"] = s.max
    if s.mean is not None:
        out["mean"] = s.mean
    if s.std_dev is not None:
        out["std_dev"] = s.std_dev
    if s.median is not None:
        out["median"] = s.median
    if s.distinct_count is not None:
        out["distinct_count"] = s.distinct_count
    if s.top_values is not None:
        out["top_values"] = list(s.top_values)
    if s.all_unique_values is not None:
        out["all_unique_values"] = list(s.all_unique_values)
    return out


def _lookup_type(info: TableInfo, name: str) -> str:
    for col in info.columns:
        if col.name == name:
            return col.type
    return "TEXT"


async def build_unstructured_map(
    connector: VectorConnector,
    llm: BaseChatModel,
    *,
    n_representative: int = 5,
) -> MapBuildResult:
    """Return a :class:`MapBuildResult` whose ``data`` is
    ``{"documents": [...]}`` ready to dump as ``unstructured.yaml``.

    For each document the connector knows about:

    1. Fetch all chunks (with embeddings).
    2. Compute the centroid (mean of vectors).
    3. Ask the connector for the ``n_representative`` chunks closest to
       the centroid (filtered to that ``file_id``) - these summarize the
       document well enough for an LLM.
    4. LLM returns YAML-shaped description (title, summary, topics, lang).

    Documents whose embeddings are missing or whose LLM/YAML step failed
    are dropped from ``data`` and recorded in ``failed_ids``.
    """
    docs = await connector.list_documents()
    out: list[dict[str, Any]] = []
    failed: list[str] = []
    for d in docs:
        entry = await _describe_document(connector, llm, d.file_id, n_representative)
        if entry is not None:
            out.append(entry)
        else:
            failed.append(d.file_id)
    return MapBuildResult(data={"documents": out}, failed_ids=failed)


async def _describe_document(
    connector: VectorConnector,
    llm: BaseChatModel,
    file_id: str,
    n: int,
) -> dict[str, Any] | None:
    chunks = await connector.get_chunks(file_id, include_embeddings=True)
    if not chunks:
        return None

    vectors = [c.embedding for c in chunks if c.embedding is not None]
    if not vectors:
        log.warning("doc %s has no embeddings; skipping", file_id)
        return None
    centroid = np.array(vectors).mean(axis=0).tolist()

    representative = await connector.similarity_search_by_vector(
        centroid, k=n, filter={"file_id": file_id}
    )
    if not representative:
        representative = chunks[:n]

    prompt = unstructured_map_prompt(file_id, representative)
    description = await _invoke_with_fallback(
        llm,
        prompt=prompt,
        json_hint=UNSTRUCTURED_JSON_HINT,
        schema=DocumentDescription,
        identity=f"doc {file_id}",
    )
    if description is None:
        return None

    return {
        "file_id": file_id,
        "title": description.title.strip(),
        "summary": description.summary.strip(),
        "topics": [str(t).strip() for t in description.topics if str(t).strip()],
        "language": description.language.strip(),
    }


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` via tmp file + rename.

    Two motivations:
      - A crash mid-write leaves either the old content or the new content,
        never a half-written file (which would yaml.safe_load-fail at next
        startup of `serve`).
      - Concurrent runs of `hara ingest` and `hara semantic-map` can't see
        a partial state.

    ``tempfile.NamedTemporaryFile`` with ``delete=False`` writes to the same
    directory as the target so ``os.replace`` is guaranteed atomic on POSIX
    (cross-device moves would not be).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        # Best-effort cleanup; if the rename failed we leave nothing behind.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def dump_structured_map(data: dict[str, Any], path: Path) -> None:
    _atomic_write_text(path, yaml.safe_dump(data, sort_keys=False, allow_unicode=True))


def dump_unstructured_map(data: dict[str, Any], path: Path) -> None:
    dump_structured_map(data, path)


def load_state(path: Path) -> set[tuple[str, str, str]]:
    """Read the ``(content_hash, target, table_or_file_id)`` snapshot saved
    at the last generation.

    Legacy 2-key state files (pre-fix, missing ``table_or_file_id``) are
    tolerated by returning an empty set — that forces a regen on the next
    run, which is safe and correctly self-heals an upgraded install.
    """
    if not path.exists():
        return set()
    raw = json.loads(path.read_text())
    out: set[tuple[str, str, str]] = set()
    for item in raw:
        if "table_or_file_id" not in item:
            # Legacy format — treat as empty so callers regen.
            return set()
        out.add((item["content_hash"], item["target"], item["table_or_file_id"]))
    return out


def save_state(path: Path, snapshot: set[tuple[str, str, str]]) -> None:
    payload = [
        {"content_hash": h, "target": t, "table_or_file_id": tid} for h, t, tid in sorted(snapshot)
    ]
    _atomic_write_text(path, json.dumps(payload, indent=2))


async def current_snapshot(store: SessionStore) -> set[tuple[str, str, str]]:
    """Return the set ``{(content_hash, target, table_or_file_id)}`` for
    every row in ``hara_ingested_files`` — the data the semantic maps
    describe.

    Including ``table_or_file_id`` is what makes the lock detect logical
    renames: the same bytes re-ingested under a new table or file_id
    yields a different snapshot tuple, so ``should_regenerate`` correctly
    reports stale.
    """
    sql_rows = await store.list_ingested_files(target="sql")
    vec_rows = await store.list_ingested_files(target="vector")
    return {(r.content_hash, "sql", r.table_or_file_id) for r in sql_rows} | {
        (r.content_hash, "vector", r.table_or_file_id) for r in vec_rows
    }


async def should_regenerate(store: SessionStore, state_file: Path) -> bool:
    return await current_snapshot(store) != load_state(state_file)
