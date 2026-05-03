"""Tests for the robust JSON-from-LLM parser used as fallback when
``with_structured_output`` is not available."""

from __future__ import annotations

from hara.utils.llm_parsing import parse_llm_json


def test_parse_plain_json_object() -> None:
    assert parse_llm_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_plain_json_array() -> None:
    assert parse_llm_json("[1, 2, 3]") == [1, 2, 3]


def test_parse_strips_markdown_fence_with_json_label() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert parse_llm_json(raw) == {"a": 1}


def test_parse_strips_markdown_fence_unlabeled() -> None:
    raw = '```\n{"a": 2}\n```'
    assert parse_llm_json(raw) == {"a": 2}


def test_parse_strips_xml_output_wrapper() -> None:
    raw = '<output>{"a": 3}</output>'
    assert parse_llm_json(raw) == {"a": 3}


def test_parse_strips_xml_json_wrapper() -> None:
    raw = '<json>{"a": 4}</json>'
    assert parse_llm_json(raw) == {"a": 4}


def test_parse_extracts_from_prose_with_balanced_braces() -> None:
    raw = 'Sure! Here is the result: {"a": 5, "nested": {"b": 6}}. Hope this helps!'
    assert parse_llm_json(raw) == {"a": 5, "nested": {"b": 6}}


def test_parse_extracts_array_from_prose() -> None:
    raw = "The topics are: [1, 2, 3]. Done."
    assert parse_llm_json(raw) == [1, 2, 3]


def test_parse_handles_braces_inside_strings() -> None:
    """Depth counter must not be confused by `{` inside a JSON string."""
    raw = '{"text": "this has } a brace inside"}'
    assert parse_llm_json(raw) == {"text": "this has } a brace inside"}


def test_parse_handles_escaped_quotes_in_strings() -> None:
    raw = '{"text": "he said \\"hi\\""}'
    assert parse_llm_json(raw) == {"text": 'he said "hi"'}


def test_parse_tolerates_literal_newlines_in_strings() -> None:
    """``strict=False`` lets the JSON contain literal \\n inside strings,
    which LLMs produce frequently."""
    raw = '{"summary": "line one\nline two"}'
    assert parse_llm_json(raw) == {"summary": "line one\nline two"}


def test_parse_returns_none_on_garbage() -> None:
    assert parse_llm_json("not json at all, not even close") is None


def test_parse_returns_none_on_empty_string() -> None:
    assert parse_llm_json("") is None


def test_parse_prefers_first_strategy_that_works() -> None:
    """Plain JSON inside `{...}` wins over fence stripping when both apply."""
    raw = '{"a": 1}'
    assert parse_llm_json(raw) == {"a": 1}
