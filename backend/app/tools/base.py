"""Typed tool contract with cache, timeout, retry, and failure mapping."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

import httpx
import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from app.core.cache import RedisCache
from app.core.config import get_settings
from app.core.exceptions import (
    ExternalServiceError,
    ToolError,
    TravelMindError,
    ValidationFailure,
)
from app.core.resilience import (
    AsyncRateLimiter,
    CircuitBreaker,
    retry_async,
)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)
logger = structlog.get_logger(__name__)
_shared_cache: RedisCache | None = None
_shared_limits: dict[str, AsyncRateLimiter] = {}
_shared_breakers: dict[str, CircuitBreaker] = {}


def _cache() -> RedisCache:
    """Return the process-wide Redis client, created on first tool use."""
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = RedisCache.from_settings()
    return _shared_cache


def _limiter(provider: str) -> AsyncRateLimiter:
    """Return the process-wide token bucket for a provider."""
    if provider not in _shared_limits:
        settings = get_settings()
        _shared_limits[provider] = AsyncRateLimiter(
            settings.provider_rate_limit_per_s,
            settings.provider_rate_limit_burst,
        )
    return _shared_limits[provider]


def _breaker(provider: str) -> CircuitBreaker:
    """Return the process-wide circuit breaker for a provider."""
    if provider not in _shared_breakers:
        settings = get_settings()
        _shared_breakers[provider] = CircuitBreaker(
            settings.circuit_breaker_failure_threshold,
            settings.circuit_breaker_reset_s,
        )
    return _shared_breakers[provider]


class Tool(ABC, Generic[InputT, OutputT]):
    """Base class for typed external tools and future agent integrations."""

    name: str
    description: str
    input_schema: type[InputT]
    output_schema: type[OutputT]
    provider: str

    def __init__(
        self,
        *,
        cache: RedisCache | None = None,
        cache_ttl_s: int = 0,
        timeout_s: float | None = None,
        retries: int | None = None,
        limiter: AsyncRateLimiter | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        """Configure execution controls, allowing test doubles to be injected."""
        settings = get_settings()
        self.cache = cache or _cache()
        self.cache_ttl_s = cache_ttl_s
        self.timeout_s = timeout_s or settings.tool_timeout_s
        self.retries = settings.tool_retries if retries is None else retries
        self.limiter = limiter or _limiter(self.provider)
        self.breaker = breaker or _breaker(self.provider)

    @abstractmethod
    async def _run(self, payload: InputT) -> OutputT:
        """Perform the provider-specific operation."""
        raise NotImplementedError

    async def run(self, payload: InputT | dict[str, object]) -> OutputT:
        """Validate, cache, execute, and normalize a tool invocation."""
        started = time.perf_counter()
        cache_hit = False
        tool_status = "error"
        try:
            try:
                request = self.input_schema.model_validate(payload)
            except ValidationError as error:
                raise ValidationFailure("Invalid tool input") from error
            cache_key = request.model_dump_json()
            if self.cache_ttl_s > 0:
                cached_value = await self.cache.get(
                    self.name, cache_key, self.output_schema
                )
                if cached_value is not None:
                    cache_hit = True
                    tool_status = "success"
                    return cached_value
                lock_key = self.cache.make_key(self.name, f"lock:{cache_key}")
                lock = self.cache.client.lock(
                    lock_key,
                    timeout=max(self.cache_ttl_s, 10),
                    blocking_timeout=5,
                )
                try:
                    acquired = await lock.acquire()
                except RedisError as error:
                    logger.warning(
                        "tool_cache_lock_failed",
                        tool=self.name,
                        error_type=type(error).__name__,
                    )
                    acquired = False
                if acquired:
                    try:
                        cached_value = await self.cache.get(
                            self.name, cache_key, self.output_schema
                        )
                        if cached_value is not None:
                            cache_hit = True
                            tool_status = "success"
                            return cached_value
                        result = await self._execute(request)
                        await self.cache.set(
                            self.name, cache_key, result, self.cache_ttl_s
                        )
                        tool_status = "success"
                        return result
                    finally:
                        try:
                            await lock.release()
                        except RedisError as error:
                            logger.warning(
                                "tool_cache_lock_release_failed",
                                tool=self.name,
                                error_type=type(error).__name__,
                            )
            result = await self._execute(request)
            tool_status = "success"
            return result
        except TravelMindError:
            raise
        except httpx.HTTPStatusError as error:
            http_status = error.response.status_code
            logger.warning(
                "tool_call_failed",
                tool=self.name,
                provider=self.provider,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                cache_hit=cache_hit,
                status="error",
                error_type=type(error).__name__,
                http_status=http_status,
            )
            if http_status < 500 and http_status != 429:
                raise ToolError(
                    f"{self.name} provider rejected the request (HTTP {http_status})"
                ) from error
            raise ExternalServiceError(
                f"{self.name} provider is temporarily unavailable"
            ) from error
        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError) as error:
            raise ExternalServiceError(
                f"{self.name} provider could not be reached"
            ) from error
        except Exception as error:
            logger.warning(
                "tool_call_failed",
                tool=self.name,
                provider=self.provider,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                cache_hit=cache_hit,
                status="error",
                error_type=type(error).__name__,
            )
            raise ToolError(f"{self.name} tool execution failed") from error
        finally:
            logger.info(
                "tool_call",
                tool=self.name,
                provider=self.provider,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                cache_hit=cache_hit,
                status=tool_status,
            )

    async def _execute(self, payload: InputT) -> OutputT:
        """Apply rate limiting, timeout, breaker, and transient-only retries."""
        settings = get_settings()

        async def attempt() -> OutputT:
            await self.limiter.acquire()
            return await self._run(payload)

        async with asyncio.timeout(self.timeout_s):
            return await self.breaker.call(
                lambda: retry_async(
                    attempt,
                    attempts=self.retries + 1,
                    base_delay_s=settings.llm_retry_base_s,
                )
            )

    async def _run_langchain(self, **kwargs: object) -> OutputT:
        """Adapt keyword-style structured tool arguments to the base run path."""
        return await self.run(kwargs)

    def as_langchain_tool(self) -> StructuredTool:
        """Return a LangChain StructuredTool that preserves wrapped execution."""
        return StructuredTool.from_function(
            name=self.name,
            description=self.description,
            args_schema=self.input_schema,
            coroutine=self._run_langchain,
        )
