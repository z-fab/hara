"""Tests for token-usage extraction + per-node latency wrapper."""

from __future__ import annotations

import time

from langchain_core.messages import AIMessage

from hara.agent.tracking import (
    extract_token_usage,
    measure_node_latency,
)


def test_extract_token_usage_from_aimessage_with_usage_metadata() -> None:
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    tu = extract_token_usage(msg)
    assert tu.input == 10
    assert tu.output == 5
    assert tu.total == 15


def test_extract_token_usage_from_response_metadata_fallback() -> None:
    """Some providers return token_usage in response_metadata.token_usage."""
    msg = AIMessage(
        content="hi",
        response_metadata={
            "token_usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
        },
    )
    tu = extract_token_usage(msg)
    assert tu.input == 8
    assert tu.output == 3
    assert tu.total == 11


def test_extract_token_usage_unknown_returns_zero() -> None:
    tu = extract_token_usage(AIMessage(content="hi"))
    assert tu.input == 0
    assert tu.output == 0
    assert tu.total == 0


def test_extract_token_usage_from_pydantic_model() -> None:
    """`with_structured_output` may return the parsed Pydantic model directly,
    in which case there's no usage info — we return zeros without crashing."""

    class FakeOut:
        pass

    tu = extract_token_usage(FakeOut())  # pyright: ignore[reportArgumentType]
    assert tu.input == 0


def test_measure_node_latency_records_duration() -> None:
    with measure_node_latency() as timer:
        time.sleep(0.02)
    assert timer.elapsed >= 0.02
    assert timer.elapsed < 1.0


def test_measure_node_latency_records_on_exception() -> None:
    """Even if the node raises, the timer should still report elapsed time."""
    try:
        with measure_node_latency() as timer:
            time.sleep(0.01)
            raise ValueError("boom")
    except ValueError:
        pass
    assert timer.elapsed >= 0.01
