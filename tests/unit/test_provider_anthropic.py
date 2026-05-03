"""Tests for Anthropic provider (no live API)."""

from __future__ import annotations

import pytest

from hara.config.settings import ProviderSettings
from hara.providers.llm.anthropic import AnthropicProvider


def test_factory_returns_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    chat = AnthropicProvider.from_config("claude-3-5-sonnet-latest", ProviderSettings())
    assert chat.__class__.__name__ == "ChatAnthropic"


def test_supports_structured_output() -> None:
    p = AnthropicProvider()
    assert p.supports_structured_output is True
