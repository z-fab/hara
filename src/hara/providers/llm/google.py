"""Google (Gemini) LLM provider via langchain-google-genai."""

from __future__ import annotations

import os

from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

from hara.config.settings import ProviderSettings
from hara.providers.llm.base import LLMProvider


class GoogleProvider(LLMProvider):
    @classmethod
    def from_config(
        cls,
        model: str,
        settings: ProviderSettings,  # noqa: ARG003
    ) -> BaseChatModel:
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY not set in environment")
        return ChatGoogleGenerativeAI(model=model, api_key=SecretStr(api_key))

    @property
    def supports_structured_output(self) -> bool:
        return True
