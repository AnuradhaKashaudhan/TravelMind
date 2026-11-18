"""PostgreSQL-backed planning service persistence and resume tests."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import TypedDict
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.agents import build_default_agent_registry
from app.core.config import Settings
from app.llm.base import LLMResponse, Message, TokenUsage
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import AgentRunStatus, TripStatus
from app.models.execution import AgentRun, TaskNode
from app.models.trip import (
    BudgetCategory,
    BudgetEntry,
    ItineraryDay,
    ItineraryItem,
    Trip,
    TripVersion,
)
from app.models.user import User
from app.orchestration.agent_base import AgentContext, AgentRegistry
from app.orchestration.checkpoint import postgres_checkpointer
from app.orchestration.events import EventType, InMemoryEventBus
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.state import PlanningState
from app.orchestration.testing import StubAgent
from app.schemas.agent_io import AgentResult
from app.services.planning_service import PlanningService
from app.tools.fake import build_fake_tool_registry
from app.tools.registry import ToolRegistry


class _UsageAgent(StubAgent):
    """Make one fake model call to verify task-scoped token accounting."""

    async def run(self, context: AgentContext) -> AgentResult:
        self.calls += 1
        response = await context.llm_router.generate(
            [{"role": "user", "content": "count these tokens"}]
        )
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data={"response": response.text},
        )


class _CheckpointState(TypedDict):
    value: int


class _Phase5FakeLLM(FakeLLMClient):
    """Return schema-compatible output based on the specialist prompt."""

    async def generate(
        self, messages: Sequence[Message], **kwargs: object
    ) -> LLMResponse:
        """Select deterministic JSON without relying on concurrent call order."""
        del kwargs
        message_text = " ".join(item["content"] for item in messages)
        if "Extract the traveler's requirements" in message_text:
            text = json.dumps(
                {
                    "destination": "Manali",
                    "origin": "Delhi",
                    "start_date": "2027-01-01",
                    "end_date": "2027-01-03",
                    "traveler_count": 2,
                    "budget": "1000",
                    "currency": "INR",
                }
            )
        elif "dependency-aware travel planning task graph" in message_text:
            text = json.dumps(
                {
                    "tasks": [
                        {"task_key": name, "agent_name": name}
                        for name in (
                            "destination",
                            "transport",
                            "accommodation",
                            "weather",
                            "activity",
                            "local_intelligence",
                            "budget",
                            "itinerary",
                            "validation",
                        )
                    ]
                }
            )
        elif "destination research specialist" in message_text:
            text = json.dumps({"destination": "Manali", "overview": "Himalayan valley"})
        elif "weather summary specialist" in message_text:
            text = json.dumps({"destination": "Manali"})
        elif "local intelligence specialist" in message_text:
            text = json.dumps({"destination": "Manali"})
        elif "activity and food curation specialist" in message_text:
            text = json.dumps({})
        elif "accommodation research specialist" in message_text:
            text = json.dumps({})
        else:
            text = json.dumps({})
        return LLMResponse(
            text=text,
            model="phase5-fake",
            provider="fake",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            latency_ms=0,
            finish_reason="stop",
        )


async def _increment_checkpoint_state(
    state: _CheckpointState,
) -> dict[str, int]:
    """Increment one value to exercise a real PostgreSQL checkpoint round trip."""
    return {"value": state["value"] + 1}


@pytest.mark.asyncio
async def test_postgres_checkpointer_round_trips_graph_state(
    db_session: AsyncSession,
) -> None:
    """The configured AsyncPostgresSaver persists and reloads a graph snapshot."""
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://travelmind:travelmind@localhost:5433/travelmind_test",
    )
    builder = StateGraph(_CheckpointState)
    builder.add_node("increment", _increment_checkpoint_state)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    async with postgres_checkpointer(database_url) as checkpointer:
        graph = builder.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": str(uuid4())}}
        result = await graph.ainvoke({"value": 2}, config)
        snapshot = await graph.aget_state(config)

    assert result["value"] == 3
    assert snapshot.values["value"] == 3


@pytest.mark.asyncio
async def test_planning_service_persists_runs_and_resumes_without_rerunning(
    db_session: AsyncSession,
) -> None:
    """Persist successful task output, token usage, and trip lifecycle in PostgreSQL."""
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://travelmind:travelmind@localhost:5433/travelmind_test",
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user = User(
        email=f"planner-{uuid4()}@example.com",
        hashed_password="test-hash",
        full_name="Planner",
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(user)
            await session.flush()
            trip = Trip(
                user_id=user.id,
                title="Rome trip",
                destination="Rome",
            )
            session.add(trip)
            await session.flush()
            trip_id = trip.id

    requirements = {
        "destination": "Rome",
        "start_date": "2027-05-01",
        "end_date": "2027-05-04",
        "traveler_count": 2,
        "budget": "1200",
        "currency": "EUR",
    }
    plan_response = {
        "tasks": [{"task_key": "destination", "agent_name": "destination"}]
    }
    settings = Settings(
        use_fake_providers=True,
        agent_task_timeout_s=1,
        agent_task_retries=0,
    )
    router = LLMRouter(
        {"fake": FakeLLMClient([json.dumps(requirements), json.dumps(plan_response)])},
        primary_provider="fake",
        settings=settings,
    )
    agent = _UsageAgent("destination")
    agents = AgentRegistry()
    agents.register(agent)
    service = PlanningService(
        session_factory,
        router,
        ToolRegistry(),
        agents,
        InMemorySaver(),
        settings=settings,
    )
    try:
        run_info = await service.start_planning(
            trip_id,
            "Plan a four-day Rome trip for two travelers.",
        )
        assert run_info.status == AgentRunStatus.SUCCEEDED
        assert run_info.task_states["destination"]["status"] == AgentRunStatus.SUCCEEDED
        async with session_factory() as session:
            root = await session.get(AgentRun, run_info.run_id)
            persisted_trip = await session.get(Trip, trip_id)
            nodes = list(
                (
                    await session.scalars(
                        select(TaskNode).where(TaskNode.trip_id == trip_id)
                    )
                ).all()
            )
            children = list(
                (
                    await session.scalars(
                        select(AgentRun).where(
                            AgentRun.supervisor_run_id == run_info.run_id
                        )
                    )
                ).all()
            )
        assert root is not None and root.status == AgentRunStatus.SUCCEEDED
        assert (
            persisted_trip is not None and persisted_trip.status == TripStatus.PLANNED
        )
        assert len(nodes) == 1 and nodes[0].status == AgentRunStatus.SUCCEEDED
        assert len(children) == 1
        assert children[0].token_usage is not None
        assert children[0].token_usage["prompt_tokens"] > 0
        assert children[0].token_usage["completion_tokens"] > 0
        assert children[0].token_usage["calls"] == 1
        assert run_info.plan is not None
        recovered_executor = WorkflowExecutor(
            agents,
            router,
            ToolRegistry(),
            InMemoryEventBus(),
            settings=settings,
            persistence=service,
        )
        resumed_state: PlanningState = {
            "trip_id": trip_id,
            "run_id": run_info.run_id,
            "user_request": "Plan a four-day Rome trip for two travelers.",
            "requirements": run_info.requirements,
            "plan": run_info.plan,
            "task_states": {},
            "results": {},
            "errors": [],
            "event_log": [],
        }
        recovered = await recovered_executor.execute(resumed_state)
        assert recovered["task_states"]["destination"]["status"] == (
            AgentRunStatus.SUCCEEDED
        )
        assert agent.calls == 1
        resumed = await service.resume_run(run_info.run_id)
        assert resumed.status == AgentRunStatus.SUCCEEDED
        assert agent.calls == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase6_nine_agent_graph_persists_runs_nodes_duration_and_usage(
    db_session: AsyncSession,
) -> None:
    """Nine-agent graph dependency, persistence, Trip.status, and event order."""
    database_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://travelmind:travelmind@localhost:5433/travelmind_test",
    )
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    user = User(
        email=f"phase5-{uuid4()}@example.com",
        hashed_password="test-hash",
        full_name="Phase 5",
    )
    async with session_factory() as session:
        async with session.begin():
            session.add(user)
            await session.flush()
            trip = Trip(user_id=user.id, title="Manali trip", destination="Manali")
            session.add(trip)
            await session.flush()
            trip_id = trip.id

    settings = Settings(
        use_fake_providers=True,
        agent_task_timeout_s=5,
        agent_task_retries=0,
    )
    router = LLMRouter(
        {"fake": _Phase5FakeLLM()},
        primary_provider="fake",
        settings=settings,
    )
    tools = build_fake_tool_registry(settings)
    agents = build_default_agent_registry(settings, router, tools)
    event_bus = InMemoryEventBus()
    service = PlanningService(
        session_factory,
        router,
        tools,
        agents,
        InMemorySaver(),
        settings=settings,
        event_bus=event_bus,
    )
    try:
        run_info = await service.start_planning(
            trip_id,
            "Plan a three-day Manali trip for two travelers.",
        )
        assert run_info.status == AgentRunStatus.SUCCEEDED
        async with session_factory() as session:
            nodes = list(
                (
                    await session.scalars(
                        select(TaskNode).where(TaskNode.trip_id == trip_id)
                    )
                ).all()
            )
            children = list(
                (
                    await session.scalars(
                        select(AgentRun).where(
                            AgentRun.supervisor_run_id == run_info.run_id
                        )
                    )
                ).all()
            )
        phase6_names = {
            "destination",
            "transport",
            "accommodation",
            "weather",
            "activity",
            "local_intelligence",
            "budget",
            "itinerary",
            "validation",
        }
        assert {node.agent_name for node in nodes} == phase6_names
        assert {child.agent_name for child in children} == phase6_names
        assert all(node.status == AgentRunStatus.SUCCEEDED for node in nodes)
        assert all(child.duration_ms is not None for child in children)
        assert all(child.token_usage for child in children)
        node_by_name = {node.agent_name: node for node in nodes}
        assert all(
            node_by_name[name].status == AgentRunStatus.SUCCEEDED
            for name in phase6_names
        )
        async with session_factory() as session:
            persisted_trip = await session.get(Trip, trip_id)
            assert persisted_trip is not None
            assert persisted_trip.status == TripStatus.PLANNED
            version = (
                await session.scalars(
                    select(TripVersion).where(TripVersion.trip_id == trip_id)
                )
            ).first()
            assert version is not None
            assert (
                await session.scalar(
                    select(TripVersion.id).where(TripVersion.id == version.id)
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(ItineraryDay.id).where(
                        ItineraryDay.trip_version_id == version.id
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(ItineraryItem.id)
                    .join(ItineraryDay)
                    .where(ItineraryDay.trip_version_id == version.id)
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(BudgetCategory.id).where(
                        BudgetCategory.trip_version_id == version.id
                    )
                )
                is not None
            )
            assert (
                await session.scalar(
                    select(BudgetEntry.id).where(
                        BudgetEntry.trip_version_id == version.id
                    )
                )
                is not None
            )
        event_types = [event.event_type for event in event_bus.events]
        assert event_types.index(EventType.RUN_STARTED) < event_types.index(
            EventType.RUN_COMPLETED
        )
        assert event_types.count(EventType.TASK_STARTED) == 9
        assert event_types.count(EventType.TASK_SUCCEEDED) == 9
    finally:
        await tools.aclose()
        await router.aclose()
        await engine.dispose()
