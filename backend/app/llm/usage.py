"""Context-local token and estimated-cost aggregation for LLM calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(slots=True)
class TokenUsageTracker:
    """Accumulate model usage for the current async execution context."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    calls: int = 0

    def record(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        cost_usd: float = 0.0,
    ) -> None:
        """Add one provider response to this tracker."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.estimated_cost_usd += cost_usd
        self.calls += 1

    def as_dict(self) -> dict[str, int | float]:
        """Return a persistence-friendly usage snapshot."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "calls": self.calls,
        }


_CURRENT_TRACKER: ContextVar[TokenUsageTracker | None] = ContextVar(
    "travelmind_token_usage_tracker", default=None
)


def current_usage_tracker() -> TokenUsageTracker | None:
    """Return the tracker bound to this task, if one is active."""
    return _CURRENT_TRACKER.get()


def bind_usage_tracker(tracker: TokenUsageTracker) -> Token[TokenUsageTracker | None]:
    """Bind a tracker to the current context and return its reset token."""
    return _CURRENT_TRACKER.set(tracker)


def reset_usage_tracker(token: Token[TokenUsageTracker | None]) -> None:
    """Restore the context value that preceded a tracker binding."""
    _CURRENT_TRACKER.reset(token)


@contextmanager
def track_usage(
    tracker: TokenUsageTracker | None = None,
) -> Iterator[TokenUsageTracker]:
    """Bind a fresh or supplied tracker for a synchronous/async call scope."""
    active_tracker = tracker or TokenUsageTracker()
    token = bind_usage_tracker(active_tracker)
    try:
        yield active_tracker
    finally:
        reset_usage_tracker(token)
