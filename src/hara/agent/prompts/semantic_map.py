"""Pydantic schemas + XML context for the semantic-map service.

The schemas (``TableDescription`` / ``DocumentDescription``) are the LLM's
output contract — passed to ``llm.with_structured_output(...)`` so the
underlying provider enforces them via JSON Schema / function calling.

The XML context (`structured_map_prompt` / `unstructured_map_prompt`) is
just the *input* to the LLM: name, columns, statistics for tables; chunk
content for documents. We don't tell the model what format to emit — the
schema does that.

User-provided strings (table/column names, chunk content) are escaped via
``xml.sax.saxutils`` so a malicious or simply weird name like ``</chunk>``
cannot break the prompt structure.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from pydantic import BaseModel, Field

from hara.connectors.sql.base import ColumnStatistics, TableInfo
from hara.connectors.vector.base import TextChunk

# ---------- Output schemas (LLM structured-output contract) ----------


class ColumnDescription(BaseModel):
    """One column entry inside ``TableDescription.columns``."""

    name: str
    type: str = ""
    description: str = ""


class TableDescription(BaseModel):
    """LLM output for one SQL table.

    Statistics are NOT in this schema: they come from the connector
    (source-of-truth) and are merged into the YAML by the service layer.
    """

    description: str
    columns: list[ColumnDescription] = Field(default_factory=list[ColumnDescription])


class DocumentDescription(BaseModel):
    """LLM output for one indexed document."""

    title: str
    summary: str
    topics: list[str] = Field(default_factory=list[str])
    language: str = ""


# ---------- XML context builders (input to the LLM) ----------


_STRUCTURED_TEMPLATE = """\
<task>
  <role>Você descreve tabelas SQL para um sistema de busca semântica.</role>
  <instructions>Use a estrutura informada (table name + columns + statistics)
    para gerar uma descrição concisa da tabela e de cada coluna. Não invente
    colunas além das listadas em table.columns.</instructions>
</task>
<table>
  <name>{name}</name>
  <row_count>{row_count}</row_count>
  <columns>
{columns}
  </columns>
</table>
"""

_UNSTRUCTURED_TEMPLATE = """\
<task>
  <role>Você descreve documentos textuais para um sistema de busca semântica.</role>
  <instructions>Use os trechos representativos abaixo para inferir título,
    resumo (2-3 frases), tópicos principais e idioma.</instructions>
</task>
<document file_id={file_id_attr}>
{chunks}
</document>
"""


# JSON-format blocks appended to the prompts when we fall back to prompt-only
# mode (provider/model doesn't support structured output). They follow the
# same XML + Portuguese style of the input templates above so the LLM sees
# a coherent prompt — same language, same structural conventions.

_JSON_CONSTRAINTS = (
    "Responda APENAS com um objeto JSON válido nesse formato. "
    "Sem prosa antes ou depois, sem cercas markdown, sem tags XML."
)

STRUCTURED_JSON_HINT = f"""

<output_format>
  <type>JSON</type>
  <schema>{{"description": "string", "columns": [{{"name": "string", "type": "string", "description": "string"}}]}}</schema>
  <constraints>{_JSON_CONSTRAINTS}</constraints>
</output_format>"""  # noqa: E501

UNSTRUCTURED_JSON_HINT = f"""

<output_format>
  <type>JSON</type>
  <schema>{{"title": "string", "summary": "string", "topics": ["string"], "language": "string"}}</schema>
  <constraints>{_JSON_CONSTRAINTS}</constraints>
</output_format>"""  # noqa: E501


def structured_map_prompt(table: TableInfo) -> str:
    cols_xml: list[str] = []
    for c in table.columns:
        attrs = f"name={quoteattr(c.name)} type={quoteattr(c.type)}"
        stats = (table.column_statistics or {}).get(c.name)
        if stats is None:
            cols_xml.append(f"    <column {attrs}/>")
        else:
            cols_xml.append(f"    <column {attrs}>{_render_stats(stats)}</column>")
    return _STRUCTURED_TEMPLATE.format(
        name=escape(table.name),
        row_count=table.row_count or 0,
        columns="\n".join(cols_xml),
    )


def _render_stats(s: ColumnStatistics) -> str:
    """Inline statistics block; only include the fields actually populated."""
    parts: list[str] = []
    if s.row_count is not None:
        parts.append(f"row_count={s.row_count}")
    if s.min is not None:
        parts.append(f"min={escape(str(s.min))}")
    if s.max is not None:
        parts.append(f"max={escape(str(s.max))}")
    if s.mean is not None:
        parts.append(f"mean={s.mean}")
    if s.distinct_count is not None:
        parts.append(f"distinct_count={s.distinct_count}")
    if not parts:
        return ""
    return f"<statistics {' '.join(parts)}/>"


def unstructured_map_prompt(file_id: str, chunks: list[TextChunk]) -> str:
    body = "\n".join(
        f'  <chunk index="{i}">{escape(c.content)}</chunk>' for i, c in enumerate(chunks)
    )
    return _UNSTRUCTURED_TEMPLATE.format(
        file_id_attr=quoteattr(file_id),
        chunks=body,
    )
