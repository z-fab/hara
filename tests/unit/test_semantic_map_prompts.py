"""Tests for semantic-map XML prompts."""

from __future__ import annotations

from hara.agent.prompts.semantic_map import (
    structured_map_prompt,
    unstructured_map_prompt,
)
from hara.connectors.sql.base import ColumnInfo, ColumnStatistics, TableInfo
from hara.connectors.vector.base import TextChunk


def test_structured_prompt_includes_table_metadata() -> None:
    info = TableInfo(
        name="producao_2024",
        columns=[
            ColumnInfo(name="municipio", type="TEXT"),
            ColumnInfo(name="producao_t", type="REAL"),
        ],
        row_count=1891,
    )
    prompt = structured_map_prompt(info)
    assert "<table" in prompt
    assert "producao_2024" in prompt
    assert "municipio" in prompt
    assert "1891" in prompt


def test_unstructured_prompt_includes_chunks_and_file_id() -> None:
    chunks = [
        TextChunk(file_id="doc_a", content="primeira parte", metadata={}),
        TextChunk(file_id="doc_a", content="segunda parte", metadata={}),
    ]
    prompt = unstructured_map_prompt("doc_a", chunks)
    assert "<document" in prompt
    assert "doc_a" in prompt
    assert "primeira parte" in prompt
    assert "segunda parte" in prompt


def test_structured_prompt_handles_missing_row_count() -> None:
    info = TableInfo(name="x", columns=[ColumnInfo(name="a", type="TEXT")], row_count=0)
    prompt = structured_map_prompt(info)
    assert "x" in prompt


def test_structured_prompt_includes_column_statistics_when_available() -> None:
    """If the connector populated column_statistics, the prompt must surface
    them so the LLM has concrete numbers to reason with."""
    info = TableInfo(
        name="t",
        columns=[ColumnInfo(name="prod", type="REAL")],
        row_count=10,
        column_statistics={"prod": ColumnStatistics(min=0.0, max=4500.0, mean=521.3)},
    )
    prompt = structured_map_prompt(info)
    assert "min" in prompt
    assert "4500" in prompt or "4500.0" in prompt


def test_prompts_escape_xml_unsafe_input() -> None:
    """A column or chunk content containing `<`, `&`, or `\"` must not break
    the XML structure of the prompt."""
    info = TableInfo(
        name="weird & <evil>",
        columns=[ColumnInfo(name='c"name', type='ty"pe')],
        row_count=1,
    )
    prompt = structured_map_prompt(info)
    assert "<evil>" not in prompt
    assert "&lt;evil&gt;" in prompt
    assert "&amp;" in prompt

    chunks = [TextChunk(file_id="x", content="</chunk>injection", metadata={})]
    p2 = unstructured_map_prompt("x", chunks)
    assert "</chunk>injection" not in p2
    assert "&lt;/chunk&gt;injection" in p2
