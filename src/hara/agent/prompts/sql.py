"""SQL generator prompt + markdown-fence stripper.

The SQL Executor node calls the *soft* LLM with this prompt; the LLM emits
plain SQL (no schema, no JSON). The node then runs:

  raw_sql = llm.ainvoke(prompt).content
  cleaned = strip_sql_fences(raw_sql)
  sql, tables = validate_and_inject_limit(cleaned, dialect, max_rows)
  result = await connector.execute_query(sql, read_only=True)

If `execute_query` raises, the node retries up to `[agent].sql_max_retries`
with `previous_error` re-fed into the prompt.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

_FENCE_RE = re.compile(r"```(?:sql)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


_PROMPT_TEMPLATE = """\
<task>
  <role>Você é um especialista em SQL. Sua tarefa é gerar UMA consulta {dialect}
    válida que responda à pergunta abaixo, usando apenas o esquema disponível.</role>
  <instructions>
    - Use APENAS as tabelas e colunas declaradas em <structured_map>.
    - NUNCA invente tabelas ou colunas.
    - Para comparações de texto, use LOWER(coluna) LIKE '%valor%'.
    - Use funções de agregação (SUM, AVG, COUNT, MIN, MAX) quando apropriado.
    - Retorne APENAS o SQL — sem explicação, sem markdown, sem prosa.
    - O SQL deve ser uma única statement SELECT (ou WITH ... SELECT).
  </instructions>
</task>

<structured_map>
{structured_map}
</structured_map>
{error_block}
<question>{question}</question>
"""


def build_sql_prompt(
    *,
    question: str,
    dialect: str,
    structured_map_yaml: str,
    previous_error: str | None,
) -> str:
    """Compose the SQL generator prompt. `previous_error` is injected on retry."""
    error_block = ""
    if previous_error:
        error_block = (
            "\n<previous_error>\n  "
            f"A consulta anterior falhou com este erro:\n  {escape(previous_error)}\n  "
            "Gere uma consulta corrigida.\n</previous_error>\n"
        )
    return _PROMPT_TEMPLATE.format(
        dialect=escape(dialect),
        structured_map=escape(structured_map_yaml or "(vazio)"),
        error_block=error_block,
        question=escape(question),
    )


def strip_sql_fences(text: str) -> str:
    """Strip ```sql ... ``` or ``` ... ``` fences if the LLM wrapped output.

    Idempotent on plain-text input. Trims trailing whitespace.
    """
    s = text.strip()
    m = _FENCE_RE.search(s)
    if m:
        return m.group(1).strip()
    return s
