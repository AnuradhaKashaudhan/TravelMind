"""Provider-independent asynchronous LLM client contract and response types."""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

Message = dict[str, str]
ModelT = TypeVar("ModelT", bound=BaseModel)


class TokenUsage(BaseModel):
    """Token counts returned by a provider."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class LLMResponse(BaseModel):
    """Normalized text-generation result and usage metadata."""

    text: str
    model: str
    provider: str
    usage: TokenUsage = TokenUsage()
    latency_ms: float
    finish_reason: str | None = None


class LLMClient(ABC):
    """Abstract async interface implemented by each model provider."""

    provider: str

    @abstractmethod
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
        """Generate a normalized text response."""
        raise NotImplementedError

    async def generate_structured(
        self,
        messages: Sequence[Message],
        schema: type[ModelT],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
        max_repairs: int = 2,
    ) -> ModelT:
        """Validate JSON output and ask the model to repair it up to twice."""
        if max_repairs < 0:
            raise ValueError("max_repairs must not be negative")
        prompt = list(messages)
        prompt.append(
            {
                "role": "system",
                "content": (
                    "Return only a JSON object matching this schema: "
                    f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
                ),
            }
        )
        last_error: ValidationError | json.JSONDecodeError | ValueError | None = None
        for repair in range(max_repairs + 1):
            response = await self.generate(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
                json_mode=True,
            )
            try:
                try:
                    return schema.model_validate_json(response.text)
                except (ValidationError, ValueError):
                    payload: Any = json.loads(response.text)
                    return schema.model_validate(payload)
            except (ValidationError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                if repair == max_repairs:
                    break
                prompt.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not match the required "
                            f"JSON schema for {schema.__name__}. Validation error: "
                            f"{str(error)[:1500]}. Return only corrected JSON."
                        ),
                    }
                )
        if last_error is None:
            raise RuntimeError("structured output repair ended without a result")
        raise ValueError(
            f"Could not produce valid {schema.__name__} after {max_repairs} repairs"
        ) from last_error

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed text inputs as vectors of exactly 768 dimensions."""
        raise NotImplementedError

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield generated text chunks."""
        raise NotImplementedError


def elapsed_ms(started_at: float) -> float:
    """Return elapsed wall-clock time in milliseconds."""
    return (time.perf_counter() - started_at) * 1000
