"""No-network coverage for the six Phase 5 research agents."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
import structlog

from app.agents.grounding import GroundingService
from app.agents.registry import build_default_agent_registry
from app.agents.research import HeuristicPricingProvider
from app.core.config import Settings
from app.llm.factory import build_llm_router
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import AgentRunStatus
from app.orchestration.agent_base import AgentContext, AgentRegistry, BaseAgent
from app.orchestration.events import InMemoryEventBus
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.state import PlanningState
from app.orchestration.supervisor import SupervisorAgent
from app.schemas.agent_io import AgentResult, SupervisorPlan, TaskSpec, TripRequirements
from app.tools.fake import build_fake_tool_registry
from app.tools.registry import ToolRegistry, build_default_tool_registry


def _context(name: str, router: LLMRouter, tools: ToolRegistry) -> AgentContext:
    """Create one isolated context with fake dependencies."""
    return AgentContext(
        state={
            "requirements": TripRequirements(
                destination="Manali",
                origin="Delhi",
                traveler_count=2,
                budget=1000,
            )
        },
        task=TaskSpec(task_key=name, agent_name=name),
        upstream_results={},
        llm_router=router,
        tool_registry=tools,
        logger=structlog.get_logger("test"),
    )


@pytest.mark.asyncio
async def test_all_phase5_agents_return_valid_results_with_fake_providers() -> None:
    """Every registered specialist returns a valid result without network calls."""
    settings = Settings(use_fake_providers=True)
    responses = [
        json.dumps({"destination": "Manali", "overview": "Mountain destination"}),
        json.dumps({"recommended_mode": "bus"}),
        json.dumps({"options": [{"name": "Snow Valley Resorts"}]}),
        json.dumps({"destination": "Manali"}),
        json.dumps({"activities": [{"title": "Mall Road"}]}),
        json.dumps({"destination": "Manali", "tips": ["Dress respectfully."]}),
    ]
    client = FakeLLMClient(responses)
    router = LLMRouter({"fake": client}, primary_provider="fake", settings=settings)
    tools = build_fake_tool_registry(settings)
    registry = build_default_agent_registry(settings, router, tools)

    for name in registry.names():
        result = await registry.get(name).run(_context(name, router, tools))
        assert result.status == AgentRunStatus.SUCCEEDED
        assert result.data is not None
        assert result.sources
        assert result.confidence is not None


class _DurationAgent(BaseAgent):
    """Small agent used to verify executor completion metadata."""

    name = "duration-test"
    description = "Executor metadata test"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a result without metadata so the executor fills it."""
        del context
        return AgentResult(agent_name=self.name, status=AgentRunStatus.SUCCEEDED)


@pytest.mark.asyncio
async def test_executor_fills_duration_and_token_usage() -> None:
    """Task and result metadata are populated when the agent omits them."""
    settings = Settings(use_fake_providers=True, agent_task_retries=0)
    router = LLMRouter(
        {"fake": FakeLLMClient()},
        primary_provider="fake",
        settings=settings,
    )
    registry = AgentRegistry()
    registry.register(_DurationAgent())
    state: PlanningState = {
        "trip_id": uuid4(),
        "run_id": uuid4(),
        "user_request": "test",
        "requirements": TripRequirements(destination="Manali"),
        "plan": SupervisorPlan(
            tasks=[TaskSpec(task_key="duration", agent_name="duration-test")]
        ),
    }
    result = await WorkflowExecutor(
        registry,
        router,
        build_fake_tool_registry(settings),
        InMemoryEventBus(),
        settings=settings,
    ).execute(state)
    task = result["task_states"]["duration"]
    agent_result = result["results"]["duration"]
    assert task["duration_ms"] is not None
    assert task["token_usage"] is not None
    assert agent_result.duration_ms is not None
    assert agent_result.token_usage is not None


def test_heuristic_pricing_provider_is_replaceable() -> None:
    """The default pricing contract exposes bounded nightly estimates."""
    low, high = HeuristicPricingProvider().estimate("3")
    assert low == 80
    assert high == 160


def test_default_plan_agent_names_exist_in_default_registry() -> None:
    """Every default-plan task resolves through the default Phase 5 registry."""
    settings = Settings(use_fake_providers=True)
    router = LLMRouter(
        {"fake": FakeLLMClient()},
        primary_provider="fake",
        settings=settings,
    )
    registry = build_default_agent_registry(
        settings, router, build_fake_tool_registry(settings)
    )
    plan = SupervisorAgent(router, registry).default_plan()
    assert {task.agent_name for task in plan.tasks} <= set(registry.names())


@pytest.mark.asyncio
async def test_fake_route_and_transport_inputs_are_schema_valid() -> None:
    """Fake route and fare tools accept the exact valid transport payloads."""
    tools = build_fake_tool_registry(Settings(use_fake_providers=True))
    route = await tools.get("get_route").run(
        {"origin": "Delhi, India", "destination": "Manali, India"}
    )
    fare = await tools.get("estimate_transport").run(
        {
            "origin": "Delhi, India",
            "destination": "Manali, India",
            "mode": "public_transport",
            "distance_m": route.distance_m,
        }
    )
    assert route.distance_m > 0
    assert fare.is_estimate is True


@pytest.mark.asyncio
async def test_grounding_drops_duplicates_and_flags_hallucinated_places() -> None:
    """Grounding merges duplicate known places and flags unknown suggestions."""
    settings = Settings(use_fake_providers=True)
    tools = build_fake_tool_registry(settings)
    router = LLMRouter(
        {"fake": FakeLLMClient()}, primary_provider="fake", settings=settings
    )
    grounded = await GroundingService().ground(
        _context("destination", router, tools),
        [
            {"name": "Hadimba Temple"},
            {"name": "Hadimba Temple"},
            {"name": "Hallucinated Sky Bridge"},
        ],
    )
    assert len(grounded) == 2
    assert grounded[0]["verified"] is True
    assert grounded[1]["verified"] is False


@pytest.mark.asyncio
async def test_places_failure_flags_unverified_without_failing_agent() -> None:
    """Missing Places tools degrade grounding to an unverified item."""
    settings = Settings(use_fake_providers=True)
    source = build_fake_tool_registry(settings)
    tools = ToolRegistry()
    for name in source.names():
        if name != "places_text_search":
            tools.register(source.get(name))
    router = LLMRouter(
        {"fake": FakeLLMClient([json.dumps({"activities": [{"title": "Mall Road"}]})])},
        primary_provider="fake",
        settings=settings,
    )
    result = (
        await build_default_agent_registry(settings, router, tools)
        .get("activity")
        .run(_context("activity", router, tools))
    )
    assert result.status == AgentRunStatus.SUCCEEDED
    assert result.data is not None
    assert result.data["activities"][0]["verified"] is False


@pytest.mark.asyncio
async def test_tool_and_llm_failures_return_degraded_valid_agent_result() -> None:
    """LLM failure still produces a valid typed result envelope."""
    settings = Settings(use_fake_providers=True)
    router = LLMRouter(
        {"fake": FakeLLMClient()}, primary_provider="fake", settings=settings
    )
    result = (
        await build_default_agent_registry(
            settings, router, build_fake_tool_registry(settings)
        )
        .get("destination")
        .run(_context("destination", router, build_fake_tool_registry(settings)))
    )
    assert result.status == AgentRunStatus.SUCCEEDED
    assert result.data is not None
    assert result.errors


@pytest.mark.asyncio
async def test_transport_and_accommodation_estimates_are_explicitly_labeled() -> None:
    """Transport and lodging outputs mark every heuristic price as an estimate."""
    settings = Settings(use_fake_providers=True)
    tools = build_fake_tool_registry(settings)
    router = LLMRouter(
        {
            "fake": FakeLLMClient(
                [
                    json.dumps({"recommended_mode": "bus"}),
                    json.dumps({"options": [{"name": "Snow Valley Resorts"}]}),
                ]
            )
        },
        primary_provider="fake",
        settings=settings,
    )
    registry = build_default_agent_registry(settings, router, tools)
    transport = await registry.get("transport").run(
        _context("transport", router, tools)
    )
    accommodation = await registry.get("accommodation").run(
        _context("accommodation", router, tools)
    )
    assert all(item["is_estimate"] for item in transport.data["options"])
    assert all(item["is_estimate"] for item in accommodation.data["options"])


@pytest.mark.asyncio
async def test_accommodation_budget_cap_and_pricing_provider_plugin() -> None:
    """Nightly cap uses total budget, nights, and travelers with injected pricing."""
    from app.agents.research import AccommodationAgent

    class FixedPricing:
        """Test pricing provider."""

        def estimate(self, price_level: str | None) -> tuple[Decimal, Decimal]:
            del price_level
            return Decimal("10"), Decimal("20")

    settings = Settings(use_fake_providers=True)
    tools = build_fake_tool_registry(settings)
    router = LLMRouter(
        {
            "fake": FakeLLMClient(
                [json.dumps({"options": [{"name": "Snow Valley Resorts"}]})]
            )
        },
        primary_provider="fake",
        settings=settings,
    )
    context = _context("accommodation", router, tools)
    context.state["requirements"] = TripRequirements(
        destination="Manali",
        start_date=date(2027, 1, 1),
        end_date=date(2027, 1, 4),
        traveler_count=2,
        budget=600,
    )
    result = await AccommodationAgent(FixedPricing()).run(context)
    assert result.data["per_night_cap"] == "100"
    assert result.data["options"][0]["estimated_cost_min"] == "10"


@pytest.mark.asyncio
async def test_weather_beyond_forecast_range_uses_estimated_fallback() -> None:
    """Weather without a forecast provider returns estimate-marked days."""
    settings = Settings(use_fake_providers=True)
    source = build_fake_tool_registry(settings)
    tools = ToolRegistry()
    tools.register(source.get("geocode"))
    router = LLMRouter(
        {"fake": FakeLLMClient([json.dumps({"destination": "Manali"})])},
        primary_provider="fake",
        settings=settings,
    )
    context = _context("weather", router, tools)
    context.state["requirements"] = TripRequirements(
        destination="Manali",
        start_date=date(2027, 1, 1),
        end_date=date(2027, 1, 3),
    )
    result = (
        await build_default_agent_registry(settings, router, tools)
        .get("weather")
        .run(context)
    )
    assert result.data is not None
    assert all(day["is_estimate"] for day in result.data["days"])


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_name",
    [
        "destination",
        "transport",
        "accommodation",
        "weather",
        "activity",
        "local_intelligence",
    ],
)
async def test_live_phase5_agent(agent_name: str) -> None:
    """Run each specialist against configured providers when explicitly enabled."""
    if not os.getenv("RUN_LIVE_TESTS"):
        pytest.skip("Set RUN_LIVE_TESTS=1 and provider keys to enable live tests.")
    settings = Settings(use_fake_providers=False)
    router = build_llm_router(settings)
    tools = build_default_tool_registry(settings)
    registry = build_default_agent_registry(settings, router, tools)
    try:
        context = _context(agent_name, router, tools)
        context.state["requirements"] = TripRequirements(
            destination="Manali, India",
            origin="Delhi, India",
            traveler_count=2,
        )
        result = await registry.get(agent_name).run(context)
        assert result.status == AgentRunStatus.SUCCEEDED
        assert result.data is not None
    finally:
        await tools.aclose()
        await router.aclose()
