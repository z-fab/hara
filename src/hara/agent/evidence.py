"""Evidence-pool builder + XML renderer for Synthesizer prompt input.

Two responsibilities:
1. Lift `sql_results` (one entry per SQL subquery, with rows[]) and
   `text_results` (one entry per text subquery, with chunks[]) into a
   single numbered list — the `evidence_pool` the Synthesizer will see
   and the orchestrator will filter via `<ref:N>`.
2. Render that pool to XML for the Synthesizer prompt.

Numbering is monotonic (`starting_id` lets the caller continue from
accumulated_evidence offset in multi-turn).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from hara.agent.state import Evidence, SqlEvidence, TextEvidence


def build_evidence_pool(
    *,
    sql_results: list[dict[str, Any]],
    text_results: list[dict[str, Any]],
    starting_id: int = 1,
) -> list[Evidence]:
    """Flatten retrieval outputs into a numbered Evidence list.

    SQL results contribute one Evidence per row; text results contribute
    one per chunk. IDs are assigned sequentially in retrieval order
    (sql first, then text — same order the Planner declared them).
    """
    out: list[Evidence] = []
    next_id = starting_id

    for sql_r in sql_results:
        rows: list[tuple[Any, ...]] = sql_r.get("rows") or []
        cols: list[str] = sql_r.get("columns") or []
        tables: list[str] = sql_r.get("tables") or []
        # Use first table as source; if multiple (JOIN), still ok — synthesizer
        # is told they're joined. v0.2 may carry the full list.
        table = tables[0] if tables else "(joined)"
        sql_query = sql_r.get("sql_query") or ""
        for row in rows:
            out.append(
                SqlEvidence(
                    evidence_id=next_id,
                    source_table=table,
                    columns=list(cols),
                    row=tuple(row),
                    sql_query=sql_query,
                )
            )
            next_id += 1

    for text_r in text_results:
        chunks: list[dict[str, Any]] = text_r.get("chunks") or []
        for ch in chunks:
            meta: dict[str, Any] = ch.get("metadata") or {}
            out.append(
                TextEvidence(
                    evidence_id=next_id,
                    file_id=str(ch.get("file_id", "unknown")),
                    content=str(ch.get("content", "")),
                    section=str(meta.get("section", "")),
                    score=ch.get("score"),
                )
            )
            next_id += 1

    return out


def render_evidence_xml(pool: Sequence[Evidence]) -> str:
    """Render the pool as XML for the Synthesizer prompt.

    The XML is the LLM's only input about retrieval — content is escaped
    so a chunk containing literal `</evidence>` cannot break the structure.
    """
    if not pool:
        return "<evidence_pool/>"

    lines: list[str] = ["<evidence_pool>"]
    for e in pool:
        if isinstance(e, SqlEvidence):
            row_text = ", ".join(
                f"{c}={escape(str(v))}" for c, v in zip(e.columns, e.row, strict=False)
            )
            lines.append(
                f'  <evidence evidence_id="{e.evidence_id}" kind="sql" '
                f"source={quoteattr(e.source_table)}>"
                f"{row_text}</evidence>"
            )
        else:
            assert isinstance(e, TextEvidence)
            lines.append(
                f'  <evidence evidence_id="{e.evidence_id}" kind="text" '
                f"source={quoteattr(e.file_id)} section={quoteattr(e.section)}>"
                f"<content>{escape(e.content)}</content></evidence>"
            )
    lines.append("</evidence_pool>")
    return "\n".join(lines)
