"""Synthesizer prompt — text streaming with `<ref:N>` markers.

No `with_structured_output` here. The LLM emits prose; the orchestrator
extracts `<ref:N>` markers via regex (markers.py) and assembles citations
deterministically. Marker protocol per spec §3.

`style` comes from `[agent].style` in hara.toml — user-defined free-form
voice ("responda em estilo formal", etc.). Default in pt-BR.
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.sax.saxutils import escape

from hara.agent.evidence import render_evidence_xml
from hara.agent.state import Evidence, Message

_PROMPT_TEMPLATE = """\
<task>
  <role>Você é um agente de SÍNTESE. Sua tarefa é responder à pergunta do
    usuário usando EXCLUSIVAMENTE as evidências fornecidas no
    <evidence_pool>. Toda afirmação factual deve ser ancorada em uma
    evidência através do marcador <ref:N>, onde N é o evidence_id.</role>
  <instructions>
    - Não invente nem alucine informação. Use apenas o que está em
      <evidence_pool>.
    - Para CADA afirmação factual, anexe um marcador <ref:N> apontando
      para a evidência que a sustenta. Ex.: "MT produziu 100 toneladas <ref:1>".
    - Você pode citar a mesma evidência mais de uma vez; cada ocorrência
      de <ref:N> é válida.
    - Se a evidência for insuficiente para responder algum aspecto, diga
      isso explicitamente — sem improvisar.
    - Responda em linguagem natural, sem repetir a pergunta.
    - Se múltiplas evidências apontarem em direções divergentes, relate
      a divergência neutralmente; não tente reconciliar inventando valores.
  </instructions>
  <style>{style}</style>
</task>

<answer_guidance>
  - Quando apresentar dados numéricos, adicione análise comparativa (rankings,
    proporções, diferenças notáveis) — essas análises são INTERPRETAÇÕES
    sustentadas pelos dados, não alucinação.
  - Quando há evolução temporal, descreva a tendência (crescimento, queda,
    estabilidade) e pontos de inflexão notáveis.
  - Anexe <ref:N> também a interpretações que dependem de evidência específica.
</answer_guidance>

{history_block}

{evidence_xml}

<user_question>{question}</user_question>
"""


def build_synthesizer_prompt(
    *,
    question: str,
    history: Sequence[Message],
    evidence_pool: Sequence[Evidence],
    style: str,
) -> str:
    """Compose the Synthesizer prompt. The LLM streams text with <ref:N>."""
    return _PROMPT_TEMPLATE.format(
        style=escape(style or "Responda de forma direta e profissional."),
        history_block=_render_history(history),
        evidence_xml=render_evidence_xml(evidence_pool),
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
