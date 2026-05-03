"""OpenAI embeddings provider via langchain-openai."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.base import EmbeddingsProvider


class OpenAIEmbeddingsProvider(EmbeddingsProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> Embeddings:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set in environment")
        kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
        if settings.base_url:
            kwargs["base_url"] = settings.base_url
        return OpenAIEmbeddings(**kwargs)

    @property
    def vector_size(self) -> int:
        # text-embedding-3-small default. For other models, override at call site.
        return 1536
