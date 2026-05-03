"""Google embeddings provider via langchain-google-genai."""

from __future__ import annotations

import os

from langchain_core.embeddings import Embeddings
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from pydantic import SecretStr

from hara.config.settings import ProviderSettings
from hara.providers.embeddings.base import EmbeddingsProvider


class GoogleEmbeddingsProvider(EmbeddingsProvider):
    @classmethod
    def from_config(
        cls,
        model: str,
        settings: ProviderSettings,  # noqa: ARG003
    ) -> Embeddings:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set in environment")
        return GoogleGenerativeAIEmbeddings(model=model, api_key=SecretStr(api_key))

    @property
    def vector_size(self) -> int:
        return 768  # text-embedding-004 default
