"""OpenAI-compatible chat and embedding client with configurable API base URL."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx
from pydantic import BaseModel, SecretStr

from app.core.config import get_settings
from app.llm.base import LLMClient, LLMResponse, Message, TokenUsage, elapsed_ms


class _Usage(BaseModel):
    """OpenAI-compatible token usage response."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class _ContentPart(BaseModel):
    """Text part in a multimodal OpenAI-compatible response."""

    type: str
    text: str | None = None


class _Message(BaseModel):
    """OpenAI-compatible chat message."""

    content: str | list[_ContentPart] | None = None


class _Choice(BaseModel):
    """OpenAI-compatible completion choice."""

    message: _Message | None = None
    finish_reason: str | None = None
    delta: _Message | None = None


class _ChatResponse(BaseModel):
    """OpenAI-compatible chat response."""

    model: str | None = None
    choices: list[_Choice] = []
    usage: _Usage = _Usage()


class _Embedding(BaseModel):
    """OpenAI-compatible embedding."""

    index: int
    embedding: list[float]


class _EmbeddingResponse(BaseModel):
    """OpenAI-compatible embedding response."""

    data: list[_Embedding]


class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI, Ollama, OpenRouter, and compatible HTTP APIs."""

    provider = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: SecretStr | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
        dimensions: int = 768,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client with caller-configurable endpoint and transport."""
        settings = get_settings()
        self.model = model or settings.openai_model
        self.embedding_model = embedding_model or settings.openai_embedding_model
        self.dimensions = dimensions
        if dimensions != 768:
            raise ValueError("Embeddings must be configured for 768 dimensions")
        key = api_key.get_secret_value() if api_key else None
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = http_client or httpx.AsyncClient(
            base_url=(
                base_url or settings.openai_base_url or "https://api.openai.com/v1"
            ),
            headers=self._headers,
        )
        self._owns_client = http_client is None

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Request a chat completion and normalize its response."""
        chosen_model = model or self.model
        started = time.perf_counter()
        payload: dict[str, Any] = {
            "model": chosen_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = await self._client.post(
            "/chat/completions",
            headers=self._headers,
            json=payload,
            timeout=timeout or get_settings().llm_timeout_s,
        )
        response.raise_for_status()
        result = _ChatResponse.model_validate(response.json())
        choice = result.choices[0] if result.choices else _Choice()
        message = choice.message
        text = _content_text(message.content if message else None)
        usage = result.usage
        return LLMResponse(
            text=text,
            model=result.model or chosen_model,
            provider=self.provider,
            usage=TokenUsage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
            ),
            latency_ms=elapsed_ms(started),
            finish_reason=choice.finish_reason,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text inputs and enforce 768-dimensional vector output."""
        if not texts:
            return []
        response = await self._client.post(
            "/embeddings",
            headers=self._headers,
            json={
                "model": self.embedding_model,
                "input": texts,
                "dimensions": self.dimensions,
            },
            timeout=get_settings().llm_timeout_s,
        )
        response.raise_for_status()
        result = _EmbeddingResponse.model_validate(response.json())
        vectors = [
            entry.embedding for entry in sorted(result.data, key=lambda x: x.index)
        ]
        for vector in vectors:
            if len(vector) != self.dimensions:
                raise ValueError(
                    f"Embedding has {len(vector)} dimensions; "
                    f"expected {self.dimensions}"
                )
        return vectors

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield incremental content from an OpenAI-compatible SSE response."""
        async with self._client.stream(
            "POST",
            "/chat/completions",
            headers=self._headers,
            json={
                "model": model or self.model,
                "messages": list(messages),
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            },
            timeout=timeout or get_settings().llm_timeout_s,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = _ChatResponse.model_validate_json(data)
                for choice in chunk.choices:
                    if choice.delta:
                        text = _content_text(choice.delta.content)
                        if text:
                            yield text

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._owns_client:
            await self._client.aclose()


def _content_text(content: str | list[_ContentPart] | None) -> str:
    """Normalize text or multimodal OpenAI-compatible content."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return "".join(part.text or "" for part in content if part.type == "text")
