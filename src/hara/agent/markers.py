"""`<ref:N>` marker extraction + citations builder.

The Synthesizer streams text containing markers like `<ref:7>` referring to
`evidence_id` 7 in the orchestrator's `evidence_pool`. After the stream
completes, we extract markers via regex and filter the pool to build
`citations[]`. The format is unique enough not to collide with prose
or markdown; the regex is anchored to digits only.

Edge cases (per spec §3):
- `<ref:7>` when pool only has 1..3 → counted in `unsupported_markers`,
  text preserved as-is.
- `<ref:1>` repeated 3x → single citation entry (dedup by id).
- Pool entry with no matching marker → not in citations.
- Malformed `<ref:>` / `<ref:abc>` → ignored.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from hara.agent.state import Evidence, SqlEvidence, TextEvidence

_MARKER_RE = re.compile(r"<ref:(\d+)>")
_DEFAULT_SNIPPET_MAX = 200


@dataclass(frozen=True)
class Citation:
    """One entry in the response's `citations[]`. Snippet is truncated for payload size."""

    evidence_id: int
    kind: Literal["sql", "text"]
    source: str  # table name or file_id
    section: str
    snippet: str


def extract_marker_ids(text: str) -> list[int]:
    """Return the list of evidence_ids referenced by `<ref:N>` in order of
    first appearance, deduplicated. Malformed markers (`<ref:>`, `<ref:abc>`)
    are excluded by the regex.
    """
    seen: set[int] = set()
    out: list[int] = []
    for m in _MARKER_RE.finditer(text):
        n = int(m.group(1))
        if n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def count_unsupported_markers(text: str, pool_ids: set[int]) -> int:
    """Count distinct `<ref:N>` whose N is NOT in `pool_ids` (unsupported markers)."""
    referenced = set(extract_marker_ids(text))
    return len(referenced - pool_ids)


def build_citations(
    text: str,
    pool: Sequence[Evidence],
    *,
    snippet_max_chars: int = _DEFAULT_SNIPPET_MAX,
) -> list[Citation]:
    """Filter `pool` to evidences cited via `<ref:N>` in `text`.

    Returns citations in the order the markers FIRST appear (not in pool order).
    Unsupported markers (id not in pool) are silently skipped — caller can
    use `count_unsupported_markers` to log the discrepancy separately.
    """
    by_id: dict[int, Evidence] = {e.evidence_id: e for e in pool}
    out: list[Citation] = []
    for n in extract_marker_ids(text):
        ev = by_id.get(n)
        if ev is None:
            continue
        out.append(_evidence_to_citation(ev, snippet_max_chars))
    return out


def _evidence_to_citation(ev: Evidence, snippet_max: int) -> Citation:
    if isinstance(ev, SqlEvidence):
        snippet = ", ".join(str(v) for v in ev.row)[:snippet_max]
        return Citation(
            evidence_id=ev.evidence_id,
            kind="sql",
            source=ev.source_table,
            section="",
            snippet=snippet,
        )
    # TextEvidence (assertion via mypy/pyright covers the union exhaustion)
    assert isinstance(ev, TextEvidence)
    return Citation(
        evidence_id=ev.evidence_id,
        kind="text",
        source=ev.file_id,
        section=ev.section,
        snippet=ev.content[:snippet_max],
    )
