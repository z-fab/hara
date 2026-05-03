"""Planner prompt + PlannerOutput schema.

The Planner reads:
- The user's current question.
- Multi-turn history (capped by [agent].max_history_turns).
- accumulated_evidence (subset of evidence from prior turns deemed useful).
- structured.yaml + unstructured.yaml (the semantic maps).

It emits a list of typed sub-queries (sql or text). Empty list = reuse
implícito — accumulated_evidence already answers the question.

Used in dual mode: ``llm.with_structured_output(PlannerOutput)`` when
supported, otherwise prompt-only with PLANNER_JSON_HINT and parse_llm_json.
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape

from pydantic import BaseModel, Field

from hara.agent.evidence import render_evidence_xml
from hara.agent.state import Evidence, Message, SubQuery


class PlannerOutput(BaseModel):
    """LLM output for the Planner. Empty list = reuse implícito."""

    subqueries: list[SubQuery] = Field(default_factory=list[SubQuery])


_PROMPT_TEMPLATE = """\
<task>
  <role>Você é um PLANNER de consultas. Sua tarefa é analisar a pergunta do
    usuário e decompô-la em sub-tarefas tipadas, cada uma direcionada a um
    tipo de fonte de dados.</role>
  <instructions>
    - Analise a pergunta e decida quais fontes de dados são necessárias.
    - Reformule sub-perguntas específicas para cada fonte.
    - Cada sub-pergunta deve ser auto-contida e em linguagem natural (NÃO escreva SQL).
    - Sugira em <sources> tabelas ou file_ids relevantes baseando-se nos
      semantic maps abaixo.
    - Se as evidências já acumuladas em turnos anteriores forem suficientes
      para responder, devolva uma lista de subqueries VAZIA. Esse é o sinal
      de reuso — o orquestrador irá direto ao Synthesizer.
  </instructions>
</task>

<data_sources>
  <sql>Use quando a sub-pergunta exige valores quantitativos vindos de tabelas SQL.</sql>
  <text>Use quando a sub-pergunta exige informação qualitativa/textual de documentos.</text>
</data_sources>

<routing_guidelines>
  Use SQL quando a pergunta puder ser plenamente respondida com dados
  quantitativos (números, rankings, agregações, evolução temporal).

  Use TEXT quando a pergunta envolve conceitos, definições, processos,
  recomendações ou descrições qualitativas.

  Use AMBOS quando a resposta completa exige RELACIONAR dados quantitativos
  com contexto qualitativo — mesmo que a pergunta pareça única.

  Exemplos — apenas SQL:
    - "Quais os 5 maiores produtores de soja?" -> ranking
    - "Como evoluiu a produção ao longo das décadas?" -> agregação temporal

  Exemplos — apenas TEXT:
    - "O que é manejo integrado de pragas?" -> definição
    - "Quais as práticas recomendadas para plantio direto?" -> recomendações

  Exemplos — AMBOS:
    - "Qual a produção de café em Rondônia e quais variedades cultivam?"
    - "Como o plantio direto impactou a produtividade da soja?"
</routing_guidelines>

<structured_map>
{structured_map}
</structured_map>

<unstructured_map>
{unstructured_map}
</unstructured_map>

{history_block}

{accumulated_block}

<user_question>{question}</user_question>
"""


PLANNER_JSON_HINT = """

<output_format>
  <type>JSON</type>
  <schema>{"subqueries": [{"id": "string", "type": "sql|text", "question": "string", "sources": ["string"]}]}</schema>
  <constraints>Responda APENAS com um objeto JSON válido nesse formato.
  Sem prosa antes ou depois, sem cercas markdown, sem tags XML.
  Para reusar evidências acumuladas, retorne {"subqueries": []} (objeto com lista vazia,
  NUNCA apenas []).</constraints>
</output_format>"""  # noqa: E501


def build_planner_prompt(
    *,
    question: str,
    history: Sequence[Message],
    accumulated_evidence: Sequence[Evidence],
    structured_map_yaml: str,
    unstructured_map_yaml: str,
) -> str:
    """Compose the full Planner prompt — XML, pt-BR, escaped user input."""
    history_block = _render_history(history)
    accumulated_block = _render_accumulated(accumulated_evidence)
    return _PROMPT_TEMPLATE.format(
        structured_map=escape(structured_map_yaml or "(vazio)"),
        unstructured_map=escape(unstructured_map_yaml or "(vazio)"),
        history_block=history_block,
        accumulated_block=accumulated_block,
        question=escape(question),
    )


def _render_history(history: Sequence[Message]) -> str:
    if not history:
        return "<history/>"
    lines = ["<history>"]
    for m in history:
        lines.append(f'  <turn role="{m.role}">{escape(m.content)}</turn>')
    lines.append("</history>")
    return "\n".join(lines)


def _render_accumulated(pool: Sequence[Evidence]) -> str:
    if not pool:
        return "<accumulated_evidence/>"
    return "<accumulated_evidence>\n" + render_evidence_xml(pool) + "\n</accumulated_evidence>"
