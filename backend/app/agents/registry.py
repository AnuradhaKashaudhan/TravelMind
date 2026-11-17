"""Construction of the stable Phase 5 specialist registry."""

from __future__ import annotations

from app.agents.research import (
    AccommodationAgent,
    ActivityAgent,
    DestinationResearchAgent,
    LocalIntelligenceAgent,
    TransportAgent,
    WeatherAgent,
)
from app.core.config import Settings
from app.llm.router import LLMRouter
from app.orchestration.agent_base import AgentRegistry
from app.tools.registry import ToolRegistry


def build_default_agent_registry(
    settings: Settings,
    llm_router: LLMRouter | None = None,
    tool_registry: ToolRegistry | None = None,
) -> AgentRegistry:
    """Register all Phase 5 agents against shared application dependencies."""
    del settings, llm_router, tool_registry
    registry = AgentRegistry()
    for agent in (
        DestinationResearchAgent(),
        TransportAgent(),
        AccommodationAgent(),
        WeatherAgent(),
        ActivityAgent(),
        LocalIntelligenceAgent(),
    ):
        registry.register(agent)
    return registry
