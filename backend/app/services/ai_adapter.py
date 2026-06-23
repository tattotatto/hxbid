"""宏曦标书 - Multi-Model AI Adapter.

Supports DeepSeek, OpenAI, and TongYi (通义千问) through a unified
OpenAI-compatible interface. Provider and model are selected via
the AI_PROVIDER config setting.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

import logging
from typing import Any, AsyncIterator, Dict, List

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Provider metadata
PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "openai": {
        "label": "OpenAI",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
    },
    "tongyi": {
        "label": "通义千问",
        "models": ["qwen-plus", "qwen-max", "qwen-turbo", "qwen-long"],
    },
}


class AIAdapter:
    """Multi-model AI adapter with lazy client initialization.

    Usage::

        from app.services.ai_adapter import ai_adapter
        result = await ai_adapter.chat_completion(messages=[...])
    """

    def __init__(self):
        self._clients: Dict[str, AsyncOpenAI] = {}

    # ------------------------------------------------------------------
    # Client management
    # ------------------------------------------------------------------

    def _get_provider_config(self, provider: str | None = None) -> tuple:
        """Return (api_key, base_url, model) for the given provider."""
        p = provider or settings.AI_PROVIDER

        if p == "deepseek":
            return settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_BASE_URL, settings.DEEPSEEK_MODEL
        elif p == "openai":
            return settings.OPENAI_API_KEY, settings.OPENAI_BASE_URL, settings.OPENAI_MODEL
        elif p == "tongyi":
            return settings.TONGYI_API_KEY, settings.TONGYI_BASE_URL, settings.TONGYI_MODEL
        else:
            raise ValueError(
                f"Unknown AI provider: {p}. Supported: {', '.join(PROVIDERS.keys())}"
            )

    def _get_client(self, provider: str | None = None) -> AsyncOpenAI:
        """Get or create an AsyncOpenAI client for *provider*."""
        p = provider or settings.AI_PROVIDER
        if p not in self._clients:
            api_key, base_url, _model = self._get_provider_config(p)
            if not api_key:
                raise ValueError(
                    f"API key not configured for provider '{p}'. "
                    f"Set {p.upper()}_API_KEY in .env or system settings."
                )
            self._clients[p] = AsyncOpenAI(api_key=api_key, base_url=base_url)
            logger.info("AI client initialized for provider '%s' (%s)", p, base_url)
        return self._clients[p]

    def get_model(self, provider: str | None = None) -> str:
        """Return the model name for the current (or specified) provider."""
        _api_key, _base_url, model = self._get_provider_config(provider)
        return model

    # ------------------------------------------------------------------
    # Provider listing (for frontend)
    # ------------------------------------------------------------------

    @staticmethod
    def list_providers() -> List[Dict[str, Any]]:
        """Return available providers with their models and configured status."""
        result = []
        for key, meta in PROVIDERS.items():
            api_key, _url, model = AIAdapter()._get_provider_config(key)
            result.append({
                "id": key,
                "label": meta["label"],
                "models": meta["models"],
                "default_model": model,
                "configured": bool(api_key),
            })
        return result

    # ------------------------------------------------------------------
    # Chat completion (non-streaming)
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Dict[str, str] | None = None,
        provider: str | None = None,
    ) -> str:
        """Send a chat completion request and return the full response text."""
        client = self._get_client(provider)
        model = self.get_model(provider)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.AI_TEMPERATURE,
            "max_tokens": max_tokens if max_tokens is not None else settings.AI_MAX_TOKENS,
        }
        if response_format:
            # Some providers use different key names
            kwargs["response_format"] = response_format

        response = await client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    # ------------------------------------------------------------------
    # Chat completion (streaming)
    # ------------------------------------------------------------------

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        provider: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream chat completion tokens one at a time."""
        client = self._get_client(provider)
        model = self.get_model(provider)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.AI_TEMPERATURE,
            "max_tokens": max_tokens if max_tokens is not None else settings.AI_MAX_TOKENS,
            "stream": True,
        }
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ------------------------------------------------------------------
    # Connectivity test
    # ------------------------------------------------------------------

    async def test_connection(self, provider: str) -> Dict[str, Any]:
        """Test connectivity to a provider with a simple ping message.

        Returns:
            {"ok": bool, "latency_ms": float, "error": str | None}
        """
        import time
        try:
            client = self._get_client(provider)
            model = self.get_model(provider)
            start = time.monotonic()
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            latency = (time.monotonic() - start) * 1000
            return {
                "ok": True,
                "latency_ms": round(latency, 1),
                "model": model,
                "error": None,
            }
        except Exception as exc:
            return {
                "ok": False,
                "latency_ms": 0,
                "model": self.get_model(provider) if provider in PROVIDERS else "unknown",
                "error": str(exc),
            }


# Singleton instance
ai_adapter = AIAdapter()
