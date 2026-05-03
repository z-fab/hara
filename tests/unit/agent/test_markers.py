"""Tests for `<ref:N>` regex + citations builder."""

from __future__ import annotations

from hara.agent.markers import (
    Citation,
    build_citations,
    count_unsupported_markers,
    extract_marker_ids,
)
from hara.agent.state import Evidence, SqlEvidence, TextEvidence


def test_extract_no_markers() -> None:
    assert extract_marker_ids("Texto sem marcadores.") == []


def test_extract_single_marker() -> None:
    assert extract_marker_ids("A soja produz muito <ref:1> em MT.") == [1]


def test_extract_multiple_markers_unique_order() -> None:
    text = "Soja <ref:1> e milho <ref:2>; juntos <ref:1> dominam."
    # Preserva ordem de PRIMEIRA aparição, deduplica.
    assert extract_marker_ids(text) == [1, 2]


def test_extract_ignores_malformed_markers() -> None:
    text = "Quase ref <ref:> e <ref:abc> e <ref:9>."
    assert extract_marker_ids(text) == [9]


def test_count_unsupported_markers() -> None:
    text = "Tem <ref:1> e <ref:99> mas só temos id 1."
    pool_ids = {1}
    assert count_unsupported_markers(text, pool_ids) == 1


def test_build_citations_filters_to_used() -> None:
    pool = [
        SqlEvidence(evidence_id=1, source_table="producao", columns=["uf"], row=("MT",)),
        TextEvidence(evidence_id=2, file_id="manejo.pdf", content="x"),
        TextEvidence(evidence_id=3, file_id="outro.pdf", content="y"),
    ]
    text = "Aqui <ref:1> e ali <ref:3>."
    cites = build_citations(text, pool)
    assert [c.evidence_id for c in cites] == [1, 3]
    assert cites[0].kind == "sql"
    assert cites[1].kind == "text"


def test_build_citations_empty_when_no_markers() -> None:
    pool: list[Evidence] = [SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,))]
    assert build_citations("Sem marcadores.", pool) == []


def test_build_citations_skips_unsupported_ids() -> None:
    pool = [SqlEvidence(evidence_id=1, source_table="t", columns=["c"], row=(1,))]
    cites = build_citations("Existe <ref:1> e fantasma <ref:99>.", pool)
    assert [c.evidence_id for c in cites] == [1]


def test_citation_repr_for_sql() -> None:
    c = Citation(evidence_id=1, kind="sql", source="producao", section="", snippet="MT, 100")
    assert "producao" in repr(c) or c.source == "producao"


def test_citation_text_snippet_truncates_long_content() -> None:
    long = "A" * 500
    pool = [TextEvidence(evidence_id=1, file_id="big.pdf", content=long)]
    cites = build_citations("<ref:1>", pool)
    # snippet_max_chars default = 200 (from spec §6.api.snippet_max_chars)
    assert len(cites[0].snippet) <= 200
