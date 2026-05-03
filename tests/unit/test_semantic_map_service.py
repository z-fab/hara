"""Tests for the semantic-map service builders.

Two fake LLM stand-ins are exercised:

- :class:`_StructuredFakeLLM` — implements ``with_structured_output`` and
  returns Pydantic instances directly. Exercises the preferred (Path A)
  code path.
- :class:`_PromptOnlyFakeLLM` — raises :class:`NotImplementedError` on
  ``with_structured_output`` and answers ``ainvoke`` with raw text.
  Exercises the prompt-only fallback (Path B), including the JSON-extraction
  helpers (markdown fences, XML wrappers, prose-embedded blocks).

Both deliberately bypass :class:`BaseChatModel`'s Pydantic state so we
don't have to fight the frozen-attribute machinery for tests.
"""

from __future__ import annotations

from typing import Any, cast

import polars as pl
import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage

from hara.agent.prompts.semantic_map import (
    ColumnDescription,
    DocumentDescription,
    TableDescription,
)
from hara.connectors.sql.memory import MemorySQLConnector
from hara.connectors.testing import FakeEmbedder
from hara.connectors.vector.base import TextChunk
from hara.connectors.vector.memory import MemoryVectorConnector
from hara.services.semantic_map import (
    MapBuildResult,
    build_structured_map,
    build_unstructured_map,
)


class _StructuredFakeLLM:
    """LLM stand-in whose ``with_structured_output(Schema)`` returns an
    async runnable that yields the pre-baked responses (or raises) in order.

    Each entry in ``responses`` is either a Pydantic model instance (when
    the runnable should return it) or an Exception (when the runnable
    should raise it). Used to exercise Path A.
    """

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def with_structured_output(self, _schema: type, **_kwargs: Any) -> Any:
        outer = self

        class _Runnable:
            async def ainvoke(self, _input: Any) -> Any:
                i = outer._idx
                outer._idx += 1
                r = outer._responses[i]
                if isinstance(r, Exception):
                    raise r
                return r

        return _Runnable()


class _PromptOnlyFakeLLM:
    """LLM stand-in that refuses structured output and responds with raw text.

    ``with_structured_output`` raises :class:`NotImplementedError` (the
    signal HARA's fallback uses to switch paths). ``ainvoke`` returns the
    next pre-baked text response (or raises). Used to exercise Path B —
    prompt-only JSON with the robust parser.
    """

    def __init__(self, text_responses: list[Any]) -> None:
        self._responses = list(text_responses)
        self._idx = 0

    def with_structured_output(self, _schema: type, **_kwargs: Any) -> Any:
        raise NotImplementedError("this fake provider doesn't support structured output")

    async def ainvoke(self, _input: Any) -> Any:
        i = self._idx
        self._idx += 1
        r = self._responses[i]
        if isinstance(r, Exception):
            raise r
        return AIMessage(content=r) if isinstance(r, str) else r


def _as_llm(fake: object) -> BaseChatModel:
    """Pretend the fake is a BaseChatModel for the call sites."""
    return cast(BaseChatModel, fake)


# ---------- structured builder ----------


@pytest.fixture
def llm_one_table() -> _StructuredFakeLLM:
    return _StructuredFakeLLM(
        [
            TableDescription(
                description="Producao agricola por municipio.",
                columns=[
                    ColumnDescription(name="municipio", type="TEXT", description="nome"),
                    ColumnDescription(name="producao_t", type="REAL", description="toneladas"),
                ],
            )
        ]
    )


async def test_build_structured_map_describes_tables(
    llm_one_table: _StructuredFakeLLM,
) -> None:
    sql = MemorySQLConnector()
    df = pl.DataFrame({"municipio": ["A", "B"], "producao_t": [1.0, 2.0]})
    await sql.upsert_table("producao_2024", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(llm_one_table))
    assert isinstance(result, MapBuildResult)
    assert result.failed_ids == []
    out: dict[str, Any] = result.data
    assert "tables" in out
    assert out["tables"][0]["table_name"] == "producao_2024"
    assert out["tables"][0]["description"] == "Producao agricola por municipio."
    assert any(c["name"] == "municipio" for c in out["tables"][0]["columns"])


async def test_build_structured_map_skips_session_tables() -> None:
    """Internal hara_* tables must not appear in the structured map."""
    fake = _StructuredFakeLLM(
        [
            TableDescription(
                description="Tabela de dados reais.",
                columns=[ColumnDescription(name="x", type="BIGINT", description="id")],
            )
        ]
    )
    sql = MemorySQLConnector()
    df = pl.DataFrame({"x": [1]})
    await sql.upsert_table("hara_threads", df, mode="replace")
    await sql.upsert_table("real_data", df, mode="replace")
    result = await build_structured_map(sql, _as_llm(fake))
    names = [t["table_name"] for t in result.data["tables"]]
    assert "real_data" in names
    assert "hara_threads" not in names


async def test_build_structured_map_drops_hallucinated_columns() -> None:
    """If the LLM invents a column not declared by the connector, it must
    be dropped from the output rather than leaking into the map."""
    fake = _StructuredFakeLLM(
        [
            TableDescription(
                description="ok",
                columns=[
                    ColumnDescription(name="a", type="BIGINT", description="real"),
                    ColumnDescription(name="ghost", type="TEXT", description="invented"),
                ],
            )
        ]
    )
    sql = MemorySQLConnector()
    df = pl.DataFrame({"a": [1]})
    await sql.upsert_table("t", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    cols = [c["name"] for c in result.data["tables"][0]["columns"]]
    assert cols == ["a"]


# ---------- unstructured builder ----------


@pytest.fixture
def llm_one_doc() -> _StructuredFakeLLM:
    return _StructuredFakeLLM(
        [
            DocumentDescription(
                title="Manual Curto",
                summary="Documento sobre algo.",
                topics=["tema", "exemplo"],
                language="Portuguese",
            )
        ]
    )


async def test_build_unstructured_map_describes_docs(
    llm_one_doc: _StructuredFakeLLM,
) -> None:
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    await vec.upsert_chunks(
        [
            TextChunk(file_id="doc_a", content="parte 1 do manual", metadata={}),
            TextChunk(file_id="doc_a", content="parte 2 do manual", metadata={}),
        ]
    )
    result = await build_unstructured_map(vec, _as_llm(llm_one_doc))
    assert isinstance(result, MapBuildResult)
    assert result.failed_ids == []
    doc = result.data["documents"][0]
    assert doc["file_id"] == "doc_a"
    assert doc["title"] == "Manual Curto"
    assert doc["topics"] == ["tema", "exemplo"]


async def test_build_unstructured_map_skips_empty_docs(
    llm_one_doc: _StructuredFakeLLM,
) -> None:
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    result = await build_unstructured_map(vec, _as_llm(llm_one_doc))
    assert result.data["documents"] == []
    assert result.failed_ids == []


# ---------- failure tracking ----------


async def test_build_structured_map_records_failed_table_ids() -> None:
    """When the structured-output call raises, the table name must surface
    in failed_ids so the CLI refuses to lock the state-snapshot."""
    fake = _StructuredFakeLLM([RuntimeError("provider down")])
    sql = MemorySQLConnector()
    df = pl.DataFrame({"x": [1, 2]})
    await sql.upsert_table("oops", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    assert result.data == {"tables": []}
    assert result.failed_ids == ["oops"]


async def test_build_unstructured_map_records_failed_doc_ids() -> None:
    fake = _StructuredFakeLLM([RuntimeError("provider down")])
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    await vec.upsert_chunks(
        [TextChunk(file_id="doc_x", content="conteudo", metadata={})],
    )
    result = await build_unstructured_map(vec, _as_llm(fake))
    assert result.data == {"documents": []}
    assert result.failed_ids == ["doc_x"]


async def test_build_unstructured_map_partial_success_lists_failures() -> None:
    """One doc succeeds + one fails: only the failure is in failed_ids,
    the success is in data. Drives the CLI's all-or-nothing lock."""
    fake = _StructuredFakeLLM(
        [
            DocumentDescription(
                title="Boa Doc",
                summary="ok",
                topics=["a"],
                language="Portuguese",
            ),
            RuntimeError("flaky provider"),
        ]
    )
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    await vec.upsert_chunks(
        [
            TextChunk(file_id="doc_ok", content="texto a", metadata={}),
            TextChunk(file_id="doc_bad", content="texto b", metadata={}),
        ]
    )
    result = await build_unstructured_map(vec, _as_llm(fake))
    successful_ids = [d["file_id"] for d in result.data["documents"]]
    assert successful_ids == ["doc_ok"]
    assert result.failed_ids == ["doc_bad"]


# ---------- prompt-only fallback (Path B) ----------


async def test_build_structured_map_falls_back_to_prompt_only() -> None:
    """When the provider doesn't support structured output, the builder
    falls back to prompt-only JSON parsing and still produces a map."""
    fake = _PromptOnlyFakeLLM(
        [
            '{"description": "Tabela de testes.", '
            '"columns": [{"name": "x", "type": "BIGINT", "description": "id"}]}'
        ]
    )
    sql = MemorySQLConnector()
    df = pl.DataFrame({"x": [1, 2]})
    await sql.upsert_table("dados", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    assert result.failed_ids == []
    table = result.data["tables"][0]
    assert table["description"] == "Tabela de testes."
    assert table["columns"][0]["name"] == "x"


async def test_build_structured_map_fallback_handles_markdown_fence() -> None:
    """The fallback path must survive ```json fences from the LLM."""
    fake = _PromptOnlyFakeLLM(
        [
            "Sure! Here is the JSON:\n\n"
            "```json\n"
            '{"description": "Producao agricola.", '
            '"columns": [{"name": "a", "type": "BIGINT", "description": "id"}]}\n'
            "```\n\nLet me know if you need anything else."
        ]
    )
    sql = MemorySQLConnector()
    df = pl.DataFrame({"a": [1]})
    await sql.upsert_table("t", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    assert result.failed_ids == []
    assert result.data["tables"][0]["description"] == "Producao agricola."


async def test_build_structured_map_fallback_validation_failure_marks_failed() -> None:
    """If the fallback parse succeeds but doesn't match the schema (e.g.,
    LLM omits a required field), the table goes to failed_ids."""
    # description is None, not str → ValidationError
    fake = _PromptOnlyFakeLLM(['{"columns": [], "description": null}'])
    sql = MemorySQLConnector()
    df = pl.DataFrame({"a": [1]})
    await sql.upsert_table("t", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    assert result.data == {"tables": []}
    assert result.failed_ids == ["t"]


async def test_build_unstructured_map_falls_back_to_prompt_only() -> None:
    fake = _PromptOnlyFakeLLM(
        [
            '<output>{"title": "Manual", "summary": "ok", '
            '"topics": ["a","b"], "language": "Portuguese"}</output>'
        ]
    )
    vec = MemoryVectorConnector(embedder=FakeEmbedder())
    await vec.upsert_chunks([TextChunk(file_id="doc_a", content="texto", metadata={})])

    result = await build_unstructured_map(vec, _as_llm(fake))
    assert result.failed_ids == []
    doc = result.data["documents"][0]
    assert doc["title"] == "Manual"
    assert doc["topics"] == ["a", "b"]


async def test_build_structured_map_fallback_garbled_response_marks_failed() -> None:
    """LLM returns prose with no extractable JSON → fallback parse returns None."""
    fake = _PromptOnlyFakeLLM(["I don't know how to describe this table, sorry."])
    sql = MemorySQLConnector()
    df = pl.DataFrame({"a": [1]})
    await sql.upsert_table("t", df, mode="replace")

    result = await build_structured_map(sql, _as_llm(fake))
    assert result.data == {"tables": []}
    assert result.failed_ids == ["t"]
