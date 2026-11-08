"""Scriptable fake LLM provider for tests and local development."""

from __future__ import annotations

import hashlib
import time
from collections.abc import AsyncIterator, Sequence

from app.llm.base import LLMClient, LLMResponse, Message, TokenUsage, elapsed_ms


class FakeLLMClient(LLMClient):
    """Return queued outputs and deterministic vectors without network access."""

    provider = "fake"

    def __init__(
        self,
        responses: Sequence[str | LLMResponse] = (),
        *,
        model: str = "fake-model",
        dimensions: int = 768,
    ) -> None:
        """Create a fake client with optional scripted generation responses."""
        self.responses = list(responses)
        self.model = model
        self.dimensions = dimensions
        self.calls: list[list[Message]] = []

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
        """Return the next scripted result, or echo the latest user content."""
        del temperature, max_tokens, timeout, json_mode
        self.calls.append(list(messages))
        started = time.perf_counter()
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, LLMResponse):
                return response
            text = response
        else:
            text = next(
                (
                    message["content"]
                    for message in reversed(messages)
                    if message["role"] == "user"
                ),
                "",
            )
        prompt_tokens = sum(len(message["content"].split()) for message in messages)
        completion_tokens = len(text.split())
        return LLMResponse(
            text=text,
            model=model or self.model,
            provider=self.provider,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            latency_ms=elapsed_ms(started),
            finish_reason="stop",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return reproducible pseudo-vectors of the configured dimension."""
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(text.encode("utf-8")).digest()
            values = [
                ((seed[index % len(seed)] / 255.0) * 2.0) - 1.0
                for index in range(self.dimensions)
            ]
            vectors.append(values)
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
        """Yield the next scripted response in word-sized chunks."""
        response = await self.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            model=model,
        )
        for chunk in response.text.split(" "):
            yield f"{chunk} "
