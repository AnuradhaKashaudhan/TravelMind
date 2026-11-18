"""Construction of the stable Phase 6 planning-agent registry."""

from __future__ import annotations

from app.agents.budget import (
    BudgetOptimizationAgent,
    BudgetOptimizer,
    load_allocation_ratios,
)
from app.agents.itinerary import ItineraryGenerationAgent
from app.agents.research import (
    AccommodationAgent,
    ActivityAgent,
    DestinationResearchAgent,
    LocalIntelligenceAgent,
    TransportAgent,
    WeatherAgent,
)
from app.agents.validation import ValidationAgent
from app.core.config import Settings
from app.llm.router import LLMRouter
from app.orchestration.agent_base import AgentRegistry
from app.tools.registry import ToolRegistry


def build_default_agent_registry(
    settings: Settings,
    llm_router: LLMRouter | None = None,
    tool_registry: ToolRegistry | None = None,
) -> AgentRegistry:
    """Register all research and Phase 6 planning agents."""
    del llm_router, tool_registry
    registry = AgentRegistry()
    for agent in (
        DestinationResearchAgent(),
        TransportAgent(),
        AccommodationAgent(),
        WeatherAgent(),
        ActivityAgent(),
        LocalIntelligenceAgent(),
        BudgetOptimizationAgent(
            BudgetOptimizer(
                load_allocation_ratios(settings.budget_allocation_config_path)
            )
        ),
        ItineraryGenerationAgent(),
        ValidationAgent(),
    ):
        registry.register(agent)
    return registry
