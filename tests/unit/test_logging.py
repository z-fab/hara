"""Tests for structlog setup."""

from __future__ import annotations

import json
import logging

import pytest
import structlog

from hara.utils.logging import configure_logging


def test_configure_logging_pretty(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging(level="INFO", format="pretty")
    logger = structlog.get_logger("test")

    with caplog.at_level(logging.INFO):
        logger.info("hello", foo="bar")

    assert any("hello" in record.message for record in caplog.records)


def test_configure_logging_json_emits_valid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="INFO", format="json")
    logger = structlog.get_logger("test")

    logger.info("hello", foo="bar")

    captured = capsys.readouterr()
    output = captured.out.strip()
    parsed = json.loads(output)
    assert parsed["event"] == "hello"
    assert parsed["foo"] == "bar"


def test_configure_logging_invalid_format_raises() -> None:
    with pytest.raises(ValueError, match="format must be"):
        configure_logging(level="INFO", format="xml")  # type: ignore[arg-type]
