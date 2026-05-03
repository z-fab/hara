"""Tests for Ollama provider (local OpenAI-compatible)."""

from __future__ import annotations

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.ollama import OllamaEmbeddingsProvider
from hara.providers.llm.ollama import OllamaProvider


def test_llm_factory_uses_local_url() -> None:
    chat = OllamaProvider.from_config(
        "llama3.1", ProviderSettings(base_url="http://localhost:11434/v1")
    )
    assert chat.__class__.__name__ == "ChatOpenAI"


def test_embeddings_factory_uses_local_url() -> None:
    emb = OllamaEmbeddingsProvider.from_config(
        "nomic-embed-text", ProviderSettings(base_url="http://localhost:11434/v1")
    )
    assert emb.__class__.__name__ == "OpenAIEmbeddings"
