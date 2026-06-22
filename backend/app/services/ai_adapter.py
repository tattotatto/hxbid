"""宏曦标书 - AI Adapter for multi-model support.

Copyright (c) 2026 云南宏曦科技有限公司. All rights reserved.
"""

from typing import AsyncIterator, List, Dict, Any
from openai import AsyncOpenAI
from app.config import settings


class AIAdapter:
    """Multi-model adapter. Currently DeepSeek; add more by extending."""

    def __init__(self):
        self._client: AsyncOpenAI | None = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_BASE_URL,
            )
        return self._client

    def get_model(self) -> str:
        if settings.AI_PROVIDER == "deepseek":
            return settings.DEEPSEEK_MODEL
        raise ValueError(f"Unknown AI provider: {settings.AI_PROVIDER}")

    async def chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Dict[str, str] | None = None,
    ) -> str:
        kwargs: Dict[str, Any] = {
            "model": self.get_model(),
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.AI_TEMPERATURE,
            "max_tokens": max_tokens if max_tokens is not None else settings.AI_MAX_TOKENS,
        }
        if response_format:
            kwargs["response_format"] = response_format

        response = await self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def chat_completion_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        kwargs: Dict[str, Any] = {
            "model": self.get_model(),
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.AI_TEMPERATURE,
            "max_tokens": max_tokens if max_tokens is not None else settings.AI_MAX_TOKENS,
            "stream": True,
        }
        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# Singleton instance
ai_adapter = AIAdapter()
