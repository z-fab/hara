"""Ollama embeddings via OpenAI-compatible API."""

from __future__ import annotations

from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.base import EmbeddingsProvider


class OllamaEmbeddingsProvider(EmbeddingsProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> Embeddings:
        if not settings.base_url:
            raise ValueError("Ollama embeddings require base_url")
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": "ollama",  # placeholder; local server typically ignores
            "base_url": settings.base_url,
        }
        return OpenAIEmbeddings(**kwargs)

    @property
    def vector_size(self) -> int:
        # nomic-embed-text default; varies by model.
        return 768
