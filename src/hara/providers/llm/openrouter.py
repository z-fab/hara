"""OpenRouter provider — OpenAI-compatible API at openrouter.ai.

Uses langchain-openai's ChatOpenAI with a custom base_url. Many models
on OpenRouter do NOT support structured outputs reliably; therefore
`supports_structured_output = False` and callers must use manual JSON
parsing.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from hara.config.settings import ProviderSettings
from hara.providers.llm.base import LLMProvider

_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterProvider(LLMProvider):
    @classmethod
    def from_config(cls, model: str, settings: ProviderSettings) -> BaseChatModel:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not set in environment")
        kwargs: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": settings.base_url or _OPENROUTER_BASE_URL,
        }
        return ChatOpenAI(**kwargs)

    @property
    def supports_structured_output(self) -> bool:
        # Conservative: many OpenRouter models lack proper json_schema support.
        return False
