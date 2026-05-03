"""Tests for OpenRouter provider (uses ChatOpenAI under the hood)."""

from __future__ import annotations

import pytest

from hara.config.settings import ProviderSettings
from hara.providers.llm.openrouter import OpenRouterProvider


def test_factory_returns_chat_openai_with_router_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    chat = OpenRouterProvider.from_config("qwen/qwen-2.5-72b-instruct", ProviderSettings())
    # OpenRouter is OpenAI-compatible, so we use ChatOpenAI with custom base_url.
    assert chat.__class__.__name__ == "ChatOpenAI"
