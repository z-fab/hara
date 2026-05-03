"""Tests for the agent's shared LLM invocation utilities.

Focus here is on `extract_text`, which normalizes the LangChain
`BaseMessage.content` shape across providers. OpenAI returns plain str;
Gemini/Anthropic-with-thinking/multimodal callers return list-of-typed-blocks.
A previous bug let a list flow into `strip_sql_fences`, crashing on
`.strip()`. This test pins the contract.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk

from hara.agent.llm_invoke import extract_text


def test_extract_text_passes_through_string() -> None:
    assert extract_text("hello") == "hello"


def test_extract_text_from_aimessage_with_string_content() -> None:
    msg = AIMessage(content="SELECT 1")
    assert extract_text(msg) == "SELECT 1"


def test_extract_text_from_gemini_typed_blocks() -> None:
    """Gemini emits content as a list of dicts with type='text'."""
    msg = AIMessage(
        content=[
            {
                "type": "text",
                "text": "SELECT uf FROM producao",
                "extras": {"signature": "Eu4LCu..."},
            }
        ]
    )
    assert extract_text(msg) == "SELECT uf FROM producao"


def test_extract_text_concatenates_multiple_text_blocks() -> None:
    msg = AIMessage(
        content=[
            {"type": "text", "text": "SELECT "},
            {"type": "text", "text": "uf "},
            {"type": "text", "text": "FROM t"},
        ]
    )
    assert extract_text(msg) == "SELECT uf FROM t"


def test_extract_text_skips_non_text_blocks() -> None:
    """Tool-use / signature-only blocks must not contribute garbage to the
    output (the prior bug rendered the dict's repr into the answer)."""
    msg = AIMessage(
        content=[
            {"type": "text", "text": "answer"},
            {"type": "tool_use", "name": "search", "input": {"q": "x"}},
        ]
    )
    assert extract_text(msg) == "answer"


def test_extract_text_handles_mixed_str_and_dict_blocks() -> None:
    msg = AIMessage(content=["plain ", {"type": "text", "text": "block"}])
    assert extract_text(msg) == "plain block"


def test_extract_text_from_chunk() -> None:
    """astream() yields AIMessageChunks — same content shape."""
    chunk = AIMessageChunk(content=[{"type": "text", "text": "tok"}])
    assert extract_text(chunk) == "tok"


def test_extract_text_empty_content_returns_empty_string() -> None:
    assert extract_text(AIMessage(content="")) == ""
    assert extract_text(AIMessage(content=[])) == ""


def test_extract_text_unknown_type_falls_back_to_str() -> None:
    """Defensive fallback for unexpected shapes — never raises."""

    class _Weird:
        content = 42

    assert extract_text(_Weird()) == "42"
