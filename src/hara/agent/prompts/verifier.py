"""Verifier prompt — signal-only quality score.

Runs once at end (only if [verifier].mode == "signal"). Single LLM call;
populates metadata.verifier; never blocks the response. Spec §3 Verifier modes.
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape

from hara.agent.evidence import render_evidence_xml
from hara.agent.state import Evidence, VerifierSignal  # re-exported

__all__ = ["VERIFIER_JSON_HINT", "VerifierSignal", "build_verifier_prompt"]


_PROMPT_TEMPLATE = """\
<task>
  <role>Você é um VERIFICADOR de qualidade. Sua tarefa é avaliar se a
    resposta gerada está sustentada pelas evidências fornecidas e se cobre
    todos os aspectos da pergunta.</role>
  <instructions>
    - Compare cada afirmação factual da resposta com as evidências.
    - Avalie se cada marcador <ref:N> ancora uma afirmação que está de fato
      contida na evidência referenciada.
    - Estime a fração de afirmações suportadas (pct_supported, valor entre 0 e 1).
    - Conte aspectos da pergunta que NÃO foram cobertos (n_missing_aspects).
    - Liste sentenças "fracas" (weak_sentences) — não suportadas, ambíguas
      ou contradizendo evidência.
    - overall_pass = true quando pct_supported ≥ 0.8 E n_missing_aspects ≤ 1.
    - NÃO reescreva a resposta — apenas avalie.
  </instructions>
  <output_fields>
    <field name="overall_pass" type="bool">Aprovação geral.</field>
    <field name="pct_supported" type="float">0..1, fração de afirmações suportadas.</field>
    <field name="n_missing_aspects" type="int">≥ 0, aspectos da pergunta não cobertos.</field>
    <field name="weak_sentences" type="list[str]">Lista de sentenças fracas (string).</field>
  </output_fields>
</task>

<user_question>{question}</user_question>

<answer>
{answer_text}
</answer>

{evidence_xml}
"""


VERIFIER_JSON_HINT = """

<output_format>
  <type>JSON</type>
  <schema>{"overall_pass": true, "pct_supported": 0.0, "n_missing_aspects": 0, "weak_sentences": ["string"]}</schema>
  <constraints>Responda APENAS com JSON válido nesse formato.
  Sem prosa antes ou depois, sem cercas markdown, sem tags XML.</constraints>
</output_format>"""  # noqa: E501


def build_verifier_prompt(
    *,
    question: str,
    answer_text: str,
    evidence_pool: Sequence[Evidence],
) -> str:
    return _PROMPT_TEMPLATE.format(
        question=escape(question),
        answer_text=escape(answer_text),
        evidence_xml=render_evidence_xml(evidence_pool),
    )
