"""Stub agents for orchestration tests and the local demo only."""

from __future__ import annotations

from typing import Any

from app.models.enums import AgentRunStatus
from app.orchestration.agent_base import (
    AgentContext,
    BaseAgent,
    TransientAgentError,
)
from app.schemas.agent_io import AgentResult


class StubAgent(BaseAgent):
    """Deterministic agent used by orchestration tests and demos."""

    def __init__(
        self,
        name: str,
        *,
        description: str = "Test-only deterministic agent",
        data: dict[str, Any] | None = None,
    ) -> None:
        """Create a stub with an optional fixed result payload."""
        self.name = name
        self.description = description
        self.data = data or {"agent": name}
        self.calls = 0

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a successful fixed result."""
        del context
        self.calls += 1
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data=self.data,
        )


class FailingAgent(BaseAgent):
    """Deterministic agent that always fails, for bounded retry tests."""

    def __init__(
        self,
        name: str,
        *,
        error: Exception | None = None,
        transient: bool = False,
    ) -> None:
        """Create an agent that raises the supplied error on every attempt."""
        self.name = name
        self.description = "Test-only failing agent"
        self.error = error or RuntimeError("stub failure")
        self.transient = transient
        self.calls = 0

    async def run(self, context: AgentContext) -> AgentResult:
        """Raise the configured failure."""
        del context
        self.calls += 1
        if self.transient and not isinstance(self.error, TransientAgentError):
            raise TransientAgentError("temporary test failure") from self.error
        raise self.error


class FlakyAgent(BaseAgent):
    """Fail transiently a fixed number of times before succeeding."""

    def __init__(self, name: str, failures: int) -> None:
        """Create a flaky agent and reject negative failure counts."""
        if failures < 0:
            raise ValueError("failures must be nonnegative")
        self.name = name
        self.description = "Test-only transiently failing agent"
        self.failures = failures
        self.calls = 0

    async def run(self, context: AgentContext) -> AgentResult:
        """Raise a retryable exception until the configured count is exhausted."""
        del context
        self.calls += 1
        if self.calls <= self.failures:
            raise TransientAgentError("temporary test failure")
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data={"attempts": self.calls},
        )
