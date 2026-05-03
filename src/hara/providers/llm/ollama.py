"""Ollama / LMStudio LLM provider (OpenAI-compatible local).

Both expose OpenAI-compatible APIs over HTTP. This single class handles
both — the discriminator is the base_url passed in via ProviderSettings.
The user chooses which to use by selecting the provider name in
[models.<node>] or [models.hard]/[models.soft]:

    [models.hard]
    provider = "ollama"      # or "lmstudio"
    model = "llama3.1"
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from hara.config.settings import ProviderSettings
from hara.providers.llm.base import LLMProvider


class OllamaProvider(LLMProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> BaseChatModel:
        if not settings.base_url:
            raise ValueError("Ollama/LMStudio provider requires base_url in settings")
        # OpenAI-compatible API; many local servers accept any non-empty key.
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": "ollama",  # placeholder; local server typically ignores
            "base_url": settings.base_url,
        }
        return ChatOpenAI(**kwargs)

    @property
    def supports_structured_output(self) -> bool:
        # Depends on the underlying model. Conservative default: False.
        return False
