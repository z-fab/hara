"""Tests for the evidence-pool builder."""

from __future__ import annotations

from hara.agent.evidence import build_evidence_pool, render_evidence_xml
from hara.agent.state import SqlEvidence, TextEvidence


def test_build_pool_from_sql_results_only() -> None:
    sql_results = [
        {
            "task_query": "produção MT",
            "sql_query": "SELECT * FROM producao WHERE uf='MT'",
            "tables": ["producao"],
            "columns": ["uf", "tons"],
            "rows": [("MT", 100), ("MT", 200)],
        }
    ]
    pool = build_evidence_pool(sql_results=sql_results, text_results=[])
    assert len(pool) == 2
    assert all(isinstance(e, SqlEvidence) for e in pool)
    assert [e.evidence_id for e in pool] == [1, 2]


def test_build_pool_from_text_results_only() -> None:
    text_results = [
        {
            "task_query": "manejo",
            "chunks": [
                {"file_id": "manejo.pdf", "content": "x", "metadata": {"section": "1"}},
                {"file_id": "manejo.pdf", "content": "y", "metadata": {}},
            ],
        }
    ]
    pool = build_evidence_pool(sql_results=[], text_results=text_results)
    assert len(pool) == 2
    assert all(isinstance(e, TextEvidence) for e in pool)
    assert [e.evidence_id for e in pool] == [1, 2]
    assert pool[0].section == "1"


def test_build_pool_combines_both_with_continuous_ids() -> None:
    sql_results = [
        {
            "task_query": "x",
            "sql_query": "SELECT 1",
            "tables": ["t"],
            "columns": ["c"],
            "rows": [("a",)],
        }
    ]
    text_results = [
        {
            "task_query": "y",
            "chunks": [{"file_id": "f", "content": "z", "metadata": {}}],
        }
    ]
    pool = build_evidence_pool(sql_results=sql_results, text_results=text_results)
    assert [e.evidence_id for e in pool] == [1, 2]


def test_build_pool_appends_to_existing_offset() -> None:
    """When reusing accumulated_evidence, new ids continue from the offset."""
    sql_results = [
        {
            "task_query": "x",
            "sql_query": "SELECT 1",
            "tables": ["t"],
            "columns": ["c"],
            "rows": [("a",), ("b",)],
        }
    ]
    pool = build_evidence_pool(sql_results=sql_results, text_results=[], starting_id=10)
    assert [e.evidence_id for e in pool] == [10, 11]


def test_render_evidence_xml_includes_id_and_source() -> None:
    pool = [
        SqlEvidence(
            evidence_id=1,
            source_table="producao",
            columns=["uf", "tons"],
            row=("MT", 100),
        ),
        TextEvidence(
            evidence_id=2,
            file_id="manejo.pdf",
            content="Texto.",
            section="2.1",
        ),
    ]
    xml = render_evidence_xml(pool)
    assert 'evidence_id="1"' in xml
    assert 'evidence_id="2"' in xml
    assert "producao" in xml
    assert "manejo.pdf" in xml
    assert "Texto." in xml
    assert "2.1" in xml


def test_render_evidence_xml_escapes_user_content() -> None:
    """Chunk content like </evidence> must not break the prompt."""
    pool = [TextEvidence(evidence_id=1, file_id="f", content="A < b > c")]
    xml = render_evidence_xml(pool)
    assert "&lt;" in xml or "<" not in xml.split("<content>")[1].split("</content>")[0]


def test_render_evidence_xml_empty_pool_returns_empty_block() -> None:
    assert render_evidence_xml([]).strip() == "<evidence_pool/>"
