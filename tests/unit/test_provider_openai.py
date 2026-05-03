"""Tests for OpenAI provider classes (no live API calls)."""

from __future__ import annotations

import pytest

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.openai import OpenAIEmbeddingsProvider
from hara.providers.llm.openai import OpenAIProvider


def test_llm_factory_returns_chat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    chat = OpenAIProvider.from_config("gpt-5-mini", ProviderSettings())
    # Class should be ChatOpenAI (we don't actually call it).
    assert chat.__class__.__name__ == "ChatOpenAI"


def test_llm_supports_structured_output() -> None:
    p = OpenAIProvider()
    assert p.supports_structured_output is True


def test_embeddings_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    emb = OpenAIEmbeddingsProvider.from_config("text-embedding-3-small", ProviderSettings())
    assert emb.__class__.__name__ == "OpenAIEmbeddings"


def test_embeddings_vector_size_known() -> None:
    p = OpenAIEmbeddingsProvider()
    # text-embedding-3-small is 1536 dims; we hardcode the default.
    assert p.vector_size == 1536
