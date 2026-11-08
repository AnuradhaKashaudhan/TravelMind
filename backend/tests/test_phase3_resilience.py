"""Focused tests for LLM routing and shared resilience primitives."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.resilience import (
    AsyncRateLimiter,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    retry_async,
)
from app.llm.base import LLMResponse, Message
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.llm.usage import TokenUsageTracker, track_usage


def _http_error(status: int) -> httpx.HTTPStatusError:
    """Create an HTTP error with a response status for retry tests."""
    request = httpx.Request("GET", "https://provider.test")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(
        "provider response", request=request, response=response
    )


async def test_retry_retries_transient_but_not_client_errors() -> None:
    """Only timeouts, rate limits, and server failures are retried."""
    transient_calls = 0

    async def transient_operation() -> str:
        nonlocal transient_calls
        transient_calls += 1
        if transient_calls == 1:
            raise _http_error(503)
        return "ok"

    assert (
        await retry_async(
            transient_operation,
            attempts=3,
            base_delay_s=0,
        )
        == "ok"
    )
    assert transient_calls == 2

    client_calls = 0

    async def client_error_operation() -> str:
        nonlocal client_calls
        client_calls += 1
        raise _http_error(400)

    with pytest.raises(httpx.HTTPStatusError):
        await retry_async(
            client_error_operation,
            attempts=3,
            base_delay_s=0,
        )
    assert client_calls == 1


async def test_circuit_breaker_state_transitions() -> None:
    """A failed threshold opens the circuit and one successful probe closes it."""
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_s=0.01)

    async def fail() -> str:
        raise RuntimeError("provider down")

    with pytest.raises(RuntimeError):
        await breaker.call(fail)
    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        await breaker.call(lambda: _return_value("blocked"))

    await asyncio.sleep(0.02)

    async def recover() -> str:
        assert breaker.state is CircuitState.HALF_OPEN
        return "healthy"

    assert await breaker.call(recover) == "healthy"
    assert breaker.state is CircuitState.CLOSED


async def _return_value(value: str) -> str:
    """Return a value from an async test callback."""
    return value


async def test_rate_limiter_enforces_token_bucket_rate() -> None:
    """A depleted bucket delays a subsequent acquire until token refill."""
    limiter = AsyncRateLimiter(rate_per_s=40, burst=1)
    await limiter.acquire()
    started = asyncio.get_running_loop().time()
    await limiter.acquire()
    assert asyncio.get_running_loop().time() - started >= 0.015


class _Answer(BaseModel):
    """Structured-output fixture model."""

    answer: str


async def test_structured_output_repairs_invalid_response() -> None:
    """The client retries invalid JSON/schema output up to the repair limit."""
    client = FakeLLMClient(["not json", '{"answer": "fixed"}'])
    result = await client.generate_structured(
        [{"role": "user", "content": "answer"}],
        _Answer,
    )
    assert result == _Answer(answer="fixed")
    assert len(client.calls) == 2
    assert "Validation error" in client.calls[1][-1]["content"]


class _FailingClient(FakeLLMClient):
    """Fake provider that fails generation to force router fallback."""

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        timeout: float | None = None,
        model: str | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        """Raise a retryable service error."""
        del messages, temperature, max_tokens, timeout, model, json_mode
        raise _http_error(429)


async def test_router_falls_back_and_tracks_token_usage() -> None:
    """Provider rate limits select fallback and usage stays context-local."""
    settings = Settings(
        llm_retries=1,
        llm_retry_base_s=0,
        provider_rate_limit_per_s=1000,
        provider_rate_limit_burst=10,
    )
    fallback = FakeLLMClient(['{"answer": "fallback"}'])
    router = LLMRouter(
        {"primary": _FailingClient(), "fallback": fallback},
        primary_provider="primary",
        fallback_provider="fallback",
        settings=settings,
        pricing_usd_per_million={"fake-model": (1.0, 2.0)},
    )
    tracker = TokenUsageTracker()
    with track_usage(tracker):
        response = await router.generate(
            [{"role": "user", "content": "hello world"}],
        )
    assert response.text == '{"answer": "fallback"}'
    assert tracker.calls == 1
    assert tracker.prompt_tokens > 0
    assert tracker.total_tokens == tracker.prompt_tokens + tracker.completion_tokens


async def test_router_applies_per_role_model_selection() -> None:
    """Configured role model overrides reach the selected provider."""
    settings = Settings(
        simple_model="cheap-role-model",
        provider_rate_limit_per_s=1000,
        provider_rate_limit_burst=10,
    )
    client = FakeLLMClient(["role output"])
    router = LLMRouter(
        {"fake": client},
        primary_provider="fake",
        settings=settings,
    )
    response = await router.generate_for_role(
        "simple",
        [{"role": "user", "content": "hello"}],
    )
    assert response.model == "cheap-role-model"


def test_fake_embedding_matches_pgvector_dimensions() -> None:
    """The local fake preserves the database's 768-dimensional contract."""
    vectors = asyncio.run(FakeLLMClient().embed(["Manali"]))
    assert len(vectors) == 1
    assert len(vectors[0]) == 768
