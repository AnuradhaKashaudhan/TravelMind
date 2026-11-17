"""Typed agent contracts and registry for orchestration tasks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.llm.router import LLMRouter
from app.schemas.agent_io import AgentResult, TaskSpec
from app.tools.registry import ToolRegistry


class AgentContext(BaseModel):
    """Runtime dependencies and immutable inputs supplied to one agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    state: dict[str, Any]
    task: TaskSpec
    upstream_results: dict[str, AgentResult]
    llm_router: LLMRouter
    tool_registry: ToolRegistry
    logger: Any
    deadline: datetime | None = None
    degraded_input_note: str | None = None


class BaseAgent(ABC):
    """Abstract async agent implementation registered by its stable name."""

    name: str
    description: str

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Execute one task and return its validated result envelope."""
        raise NotImplementedError


class AgentRegistry:
    """Register and resolve named agents without coupling orchestration to them."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._agents: dict[str, BaseAgent] = {}

    def register(self, agent: BaseAgent) -> None:
        """Add an agent, rejecting ambiguous duplicate names."""
        if not agent.name.strip():
            raise ValueError("Agent name must not be empty")
        if agent.name in self._agents:
            raise ValueError(f"Agent is already registered: {agent.name}")
        self._agents[agent.name] = agent

    def get(self, name: str) -> BaseAgent:
        """Resolve an agent by its exact registered name."""
        try:
            return self._agents[name]
        except KeyError as error:
            raise KeyError(f"Agent is not registered: {name}") from error

    def names(self) -> list[str]:
        """Return registered agent names in registration order."""
        return list(self._agents)

    def catalog(self) -> list[tuple[str, str]]:
        """Return each registered agent name and description."""
        return [(agent.name, agent.description) for agent in self._agents.values()]


class TransientAgentError(Exception):
    """Explicit marker for agent failures that may safely be retried."""
