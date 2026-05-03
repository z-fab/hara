"""Tests for Google (Gemini) provider classes."""

from __future__ import annotations

import pytest

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.google import GoogleEmbeddingsProvider
from hara.providers.llm.google import GoogleProvider


def test_llm_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    chat = GoogleProvider.from_config("gemini-2.5-pro", ProviderSettings())
    assert "Google" in chat.__class__.__name__ or "Gemini" in chat.__class__.__name__


def test_embeddings_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    emb = GoogleEmbeddingsProvider.from_config("models/text-embedding-004", ProviderSettings())
    assert "Google" in emb.__class__.__name__
