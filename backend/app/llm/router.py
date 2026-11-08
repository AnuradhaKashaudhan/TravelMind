"""Provider routing, bounded resilience, model roles, and usage accounting."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import TypeVar

import structlog
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceError
from app.core.resilience import (
    AsyncRateLimiter,
    CircuitBreaker,
    retry_async,
)
from app.llm.base import LLMClient, LLMResponse, Message
from app.llm.usage import current_usage_tracker

ModelT = TypeVar("ModelT", bound=BaseModel)
logger = structlog.get_logger(__name__)


class LLMRouter(LLMClient):
    """Route calls to a primary LLM provider with a bounded fallback."""

    provider = "router"

    def __init__(
        self,
        clients: dict[str, LLMClient],
        *,
        primary_provider: str,
        fallback_provider: str | None = None,
        settings: Settings | None = None,
        rate_limiter: AsyncRateLimiter | None = None,
        pricing_usd_per_million: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        """Create a router around named provider clients."""
        if primary_provider not in clients:
            raise ValueError(f"Unknown primary LLM provider: {primary_provider}")
        if fallback_provider is not None and fallback_provider not in clients:
            raise ValueError(f"Unknown fallback LLM provider: {fallback_provider}")
        self.clients = clients
        self.primary_provider = primary_provider
        self.fallback_provider = fallback_provider
        self.settings = settings or get_settings()
        self.rate_limiters = {
            name: rate_limiter
            or AsyncRateLimiter(
                self.settings.provider_rate_limit_per_s,
                self.settings.provider_rate_limit_burst,
            )
            for name in clients
        }
        self.pricing_usd_per_million = pricing_usd_per_million or {}
        self.breakers = {
            name: CircuitBreaker(
                self.settings.circuit_breaker_failure_threshold,
                self.settings.circuit_breaker_reset_s,
            )
            for name in clients
        }

    def _providers(self) -> list[str]:
        """Return primary and distinct configured fallback provider names."""
        names = [self.primary_provider]
        if self.fallback_provider and self.fallback_provider != self.primary_provider:
            names.append(self.fallback_provider)
        return names

    def _model_for_role(self, provider: str, role: str | None) -> str | None:
        """Resolve an optional per-role model, otherwise use provider defaults."""
        if role is None:
            return None
        if role not in {"simple", "supervisor", "itinerary"}:
            raise ValueError(f"Unsupported LLM role: {role}")
        configured = getattr(self.settings, f"{role}_model")
        return configured or None

    async def generate(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
        json_mode: bool = False,
        role: str | None = None,
    ) -> LLMResponse:
        """Generate with retries and provider fallback under one call timeout."""
        effective_timeout = timeout or self.settings.llm_timeout_s
        failures: list[Exception] = []
        for provider in self._providers():
            client = self.clients[provider]
            chosen_model = model or self._model_for_role(provider, role)

            async def invoke(
                provider_name: str = provider,
                provider_client: LLMClient = client,
                model_name: str | None = chosen_model,
            ) -> LLMResponse:
                await self.rate_limiters[provider_name].acquire()
                return await provider_client.generate(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=effective_timeout,
                    model=model_name,
                    json_mode=json_mode,
                )

            try:
                async with asyncio.timeout(effective_timeout):
                    response = await self.breakers[provider].call(
                        lambda: retry_async(
                            invoke,
                            attempts=self.settings.llm_retries + 1,
                            base_delay_s=self.settings.llm_retry_base_s,
                        )
                    )
                self._track_usage(response)
                return response
            except Exception as error:
                failures.append(error)
                logger.warning(
                    "llm_provider_failed",
                    provider=provider,
                    error_type=type(error).__name__,
                    fallback_available=provider != self._providers()[-1],
                )
        raise ExternalServiceError(
            "All configured language model providers failed"
        ) from failures[-1]

    async def generate_for_role(
        self,
        role: str,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Generate using a role-specific model override when configured."""
        return await self.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            role=role,
        )

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
        role: str | None = None,
    ) -> ModelT:
        """Validate structured output with retries routed through providers."""
        if max_repairs < 0:
            raise ValueError("max_repairs must not be negative")
        if role is None:
            return await super().generate_structured(
                messages,
                schema,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
                max_repairs=max_repairs,
            )
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
        last_error: Exception | None = None
        for repair in range(max_repairs + 1):
            response = await self.generate(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                model=model,
                json_mode=True,
                role=role,
            )
            try:
                return schema.model_validate_json(response.text)
            except ValueError as error:
                last_error = error
                if repair == max_repairs:
                    break
                prompt.append(
                    {
                        "role": "user",
                        "content": (
                            f"Correct the JSON to satisfy {schema.__name__}: "
                            f"{str(error)[:1500]}. Return only corrected JSON."
                        ),
                    }
                )
        raise ValueError(
            f"Could not produce valid {schema.__name__} after {max_repairs} repairs"
        ) from last_error

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed using primary, then fallback provider on failure."""
        failures: list[Exception] = []
        for provider in self._providers():
            client = self.clients[provider]

            async def invoke(
                provider_name: str = provider,
                provider_client: LLMClient = client,
            ) -> list[list[float]]:
                await self.rate_limiters[provider_name].acquire()
                return await provider_client.embed(texts)

            try:
                async with asyncio.timeout(self.settings.llm_timeout_s):
                    vectors = await self.breakers[provider].call(
                        lambda: retry_async(
                            invoke,
                            attempts=self.settings.llm_retries + 1,
                            base_delay_s=self.settings.llm_retry_base_s,
                        )
                    )
                if any(len(vector) != 768 for vector in vectors):
                    raise ValueError("Provider returned an embedding not sized 768")
                return vectors
            except Exception as error:
                failures.append(error)
                logger.warning(
                    "llm_embedding_provider_failed",
                    provider=provider,
                    error_type=type(error).__name__,
                )
        raise ExternalServiceError(
            "All configured embedding providers failed"
        ) from failures[-1]

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream from primary; use fallback only if primary fails before output."""
        effective_timeout = timeout or self.settings.llm_timeout_s
        providers = self._providers()
        for index, provider in enumerate(providers):
            client = self.clients[provider]
            emitted = False
            try:
                async with asyncio.timeout(effective_timeout):
                    await self.rate_limiters[provider].acquire()
                    async for chunk in client.stream(
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                        model=model or self._model_for_role(provider, None),
                    ):
                        emitted = True
                        yield chunk
                return
            except Exception as error:
                logger.warning(
                    "llm_stream_provider_failed",
                    provider=provider,
                    error_type=type(error).__name__,
                )
                if emitted or index == len(providers) - 1:
                    raise ExternalServiceError(
                        "Language model streaming failed"
                    ) from error

    def _track_usage(self, response: LLMResponse) -> None:
        """Record provider usage in the current async execution context."""
        tracker = current_usage_tracker()
        if tracker is None:
            return
        prompt_rate, completion_rate = self.pricing_usd_per_million.get(
            response.model,
            (
                self.settings.llm_input_cost_per_million_usd,
                self.settings.llm_output_cost_per_million_usd,
            ),
        )
        cost = (
            response.usage.prompt_tokens * prompt_rate
            + response.usage.completion_tokens * completion_rate
        ) / 1_000_000
        tracker.record(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            cost_usd=cost,
        )
