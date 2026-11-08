"""Google Gemini implementation using the Generative Language REST API."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, SecretStr

from app.core.config import get_settings
from app.llm.base import LLMClient, LLMResponse, Message, TokenUsage, elapsed_ms


class _GeminiPart(BaseModel):
    """Gemini content part."""

    text: str | None = None


class _GeminiContent(BaseModel):
    """Gemini content container."""

    parts: list[_GeminiPart] = []


class _GeminiCandidate(BaseModel):
    """Gemini candidate response."""

    content: _GeminiContent | None = None
    finishReason: str | None = None


class _GeminiUsage(BaseModel):
    """Gemini usage metadata."""

    promptTokenCount: int = 0
    candidatesTokenCount: int = 0
    totalTokenCount: int = 0


class _GeminiResponse(BaseModel):
    """Gemini generation response."""

    candidates: list[_GeminiCandidate] = []
    usageMetadata: _GeminiUsage = _GeminiUsage()


class _GeminiEmbedding(BaseModel):
    """Gemini embedding result."""

    values: list[float]


class _GeminiEmbeddingResponse(BaseModel):
    """Gemini embedding endpoint response."""

    embedding: _GeminiEmbedding


class GeminiClient(LLMClient):
    """Gemini provider with normalized output and enforced vector dimensions."""

    provider = "gemini"

    def __init__(
        self,
        api_key: SecretStr,
        *,
        model: str | None = None,
        embedding_model: str | None = None,
        dimensions: int = 768,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a Gemini client with an injectable HTTP transport."""
        self._api_key = api_key.get_secret_value()
        settings = get_settings()
        self.model = model or settings.gemini_model
        self.embedding_model = embedding_model or settings.gemini_embedding_model
        self.dimensions = dimensions
        if dimensions != 768:
            raise ValueError("Gemini embeddings must be configured for 768 dimensions")
        self._headers = {"x-goog-api-key": self._api_key}
        self._client = http_client or httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com/v1beta",
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
        """Generate content, requesting JSON mode for structured output prompts."""
        chosen_model = model or self.model
        contents, system_instruction = _to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        started = time.perf_counter()
        response = await self._client.post(
            f"/models/{quote(chosen_model, safe='')}:generateContent",
            headers=self._headers,
            json=body,
            timeout=timeout or get_settings().llm_timeout_s,
        )
        response.raise_for_status()
        result = _GeminiResponse.model_validate(response.json())
        text = "".join(
            part.text or ""
            for candidate in result.candidates
            if candidate.content
            for part in candidate.content.parts
        )
        usage = result.usageMetadata
        return LLMResponse(
            text=text,
            model=chosen_model,
            provider=self.provider,
            usage=TokenUsage(
                prompt_tokens=usage.promptTokenCount,
                completion_tokens=usage.candidatesTokenCount,
                total_tokens=usage.totalTokenCount,
            ),
            latency_ms=elapsed_ms(started),
            finish_reason=(
                result.candidates[0].finishReason if result.candidates else None
            ),
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text inputs and enforce compatibility with pgvector."""
        embeddings: list[list[float]] = []
        for text in texts:
            response = await self._client.post(
                f"/models/{quote(self.embedding_model, safe='')}:embedContent",
                headers=self._headers,
                json={
                    "model": f"models/{self.embedding_model}",
                    "content": {"parts": [{"text": text}]},
                    "outputDimensionality": self.dimensions,
                },
                timeout=get_settings().llm_timeout_s,
            )
            response.raise_for_status()
            result = _GeminiEmbeddingResponse.model_validate(response.json())
            vector = result.embedding.values
            _validate_vector(vector, self.dimensions)
            embeddings.append(vector)
        return embeddings

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield Gemini server-sent generation chunks."""
        chosen_model = model or self.model
        contents, system_instruction = _to_gemini_contents(messages)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        async with self._client.stream(
            "POST",
            f"/models/{quote(chosen_model, safe='')}:streamGenerateContent?alt=sse",
            headers=self._headers,
            json=body,
            timeout=timeout or get_settings().llm_timeout_s,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = _GeminiResponse.model_validate_json(line[5:].strip())
                for candidate in chunk.candidates:
                    if candidate.content:
                        for part in candidate.content.parts:
                            if part.text:
                                yield part.text

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._owns_client:
            await self._client.aclose()


def _to_gemini_contents(
    messages: Sequence[Message],
) -> tuple[list[dict[str, Any]], str | None]:
    """Convert role/content messages to Gemini's contents representation."""
    contents: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for message in messages:
        role = message["role"]
        if role == "system":
            system_parts.append(message["content"])
        else:
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": message["content"]}],
                }
            )
    return contents, "\n\n".join(system_parts) or None


def _validate_vector(vector: list[float], dimensions: int) -> None:
    """Reject embeddings incompatible with the database vector column."""
    if len(vector) != dimensions:
        raise ValueError(
            f"Embedding has {len(vector)} dimensions; expected {dimensions}"
        )
