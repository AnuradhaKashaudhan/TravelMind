"""Reusable async resilience primitives for external provider calls."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

import httpx

T = TypeVar("T")


def is_transient_error(error: Exception) -> bool:
    """Return whether an exception represents a retryable provider failure."""
    if isinstance(error, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    return status_code == 429 or (
        isinstance(status_code, int) and 500 <= status_code < 600
    )


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay_s: float = 0.25,
    max_delay_s: float = 4.0,
    retry_if: Callable[[Exception], bool] = is_transient_error,
) -> T:
    """Retry transient failures a bounded number of times with jitter."""
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    for attempt in range(attempts):
        try:
            return await operation()
        except Exception as error:
            if attempt + 1 >= attempts or not retry_if(error):
                raise
            ceiling = min(max_delay_s, base_delay_s * (2**attempt))
            await asyncio.sleep(random.uniform(ceiling / 2, ceiling))
    raise RuntimeError("unreachable retry state")


class AsyncRateLimiter:
    """A concurrency-safe token bucket limiter."""

    def __init__(self, rate_per_s: float, burst: int) -> None:
        """Create a token bucket with the given refill rate and capacity."""
        if rate_per_s <= 0 or burst < 1:
            raise ValueError("rate_per_s and burst must be positive")
        self.rate_per_s = rate_per_s
        self.burst = float(burst)
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until one request token is available."""
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.burst,
                    self._tokens + (now - self._updated_at) * self.rate_per_s,
                )
                self._updated_at = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_s = (1 - self._tokens) / self.rate_per_s
            await asyncio.sleep(wait_s)


class CircuitState(StrEnum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a circuit breaker prevents a provider call."""


class CircuitBreaker:
    """Circuit breaker that opens after consecutive failures."""

    def __init__(self, failure_threshold: int = 5, reset_timeout_s: float = 30) -> None:
        """Create a breaker with a failure threshold and reset timeout."""
        if failure_threshold < 1 or reset_timeout_s <= 0:
            raise ValueError("failure_threshold and reset_timeout_s must be positive")
        self.failure_threshold = failure_threshold
        self.reset_timeout_s = reset_timeout_s
        self.state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False
        self._lock = asyncio.Lock()

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run an operation while enforcing the breaker state machine."""
        async with self._lock:
            if self.state is CircuitState.OPEN:
                if (
                    self._opened_at is None
                    or time.monotonic() - self._opened_at < self.reset_timeout_s
                ):
                    raise CircuitOpenError("provider circuit is open")
                self.state = CircuitState.HALF_OPEN
                self._half_open_in_flight = True
            elif self.state is CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    raise CircuitOpenError("provider circuit is testing recovery")
                self._half_open_in_flight = True
        try:
            result = await operation()
        except Exception:
            async with self._lock:
                self._failures += 1
                self._half_open_in_flight = False
                if (
                    self.state is CircuitState.HALF_OPEN
                    or self._failures >= self.failure_threshold
                ):
                    self.state = CircuitState.OPEN
                    self._opened_at = time.monotonic()
            raise
        async with self._lock:
            self._failures = 0
            self.state = CircuitState.CLOSED
            self._opened_at = None
            self._half_open_in_flight = False
        return result
