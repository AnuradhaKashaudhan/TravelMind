"""Focused tests for planning graphs, execution scheduling, and safety gates."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from app.core.config import Settings
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import (
    AgentRunStatus,
    ApprovalActionType,
)
from app.orchestration.agent_base import (
    AgentContext,
    AgentRegistry,
    BaseAgent,
)
from app.orchestration.approvals import ApprovalGate
from app.orchestration.events import EventType, InMemoryEventBus
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.graph import build_planning_graph
from app.orchestration.state import PlanningState, TaskExecutionState
from app.orchestration.supervisor import SupervisorAgent
from app.orchestration.task_graph import (
    affected_downstream,
    blocked_tasks,
    ready_tasks,
    topological_order,
)
from app.orchestration.testing import FailingAgent, FlakyAgent, StubAgent
from app.schemas.agent_io import (
    AgentResult,
    SupervisorPlan,
    TaskSpec,
    TripRequirements,
)
from app.tools.base import Tool
from app.tools.registry import ToolRegistry


def _plan(*tasks: TaskSpec) -> SupervisorPlan:
    """Create a validated supervisor plan for unit tests."""
    return SupervisorPlan(tasks=list(tasks))


def _router(
    responses: list[str] | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[LLMRouter, FakeLLMClient]:
    """Build a no-network LLM router and expose its fake client for assertions."""
    config = settings or Settings(
        use_fake_providers=True,
        agent_task_retries=1,
        agent_task_retry_base_s=0,
    )
    fake = FakeLLMClient(responses or [])
    return LLMRouter(
        {"fake": fake},
        primary_provider="fake",
        settings=config,
    ), fake


def _state(plan: SupervisorPlan) -> PlanningState:
    """Return a minimal executor state with stable identifiers."""
    return {
        "trip_id": uuid4(),
        "run_id": uuid4(),
        "user_request": "Plan a city trip",
        "requirements": TripRequirements(destination="Rome"),
        "plan": plan,
        "task_states": {},
        "results": {},
        "clarification_questions": [],
        "approval_requests": {},
        "errors": [],
        "event_log": [],
    }


def test_supervisor_plan_rejects_duplicate_and_missing_dependency_keys() -> None:
    """The graph validator identifies both duplicate and unknown keys."""
    with pytest.raises(ValidationError, match="duplicate task_key"):
        SupervisorPlan(
            tasks=[
                TaskSpec(task_key="same", agent_name="a"),
                TaskSpec(task_key="same", agent_name="b"),
            ]
        )
    with pytest.raises(ValidationError, match="missing dependency key"):
        SupervisorPlan(
            tasks=[TaskSpec(task_key="a", agent_name="a", depends_on=["absent"])]
        )


def test_supervisor_plan_rejects_self_dependency_and_cycles() -> None:
    """Self-links and multi-node cycles are rejected with involved keys."""
    with pytest.raises(ValidationError, match="self-dependency"):
        SupervisorPlan(tasks=[TaskSpec(task_key="a", agent_name="a", depends_on=["a"])])
    with pytest.raises(ValidationError, match="cycle key"):
        SupervisorPlan(
            tasks=[
                TaskSpec(task_key="a", agent_name="a", depends_on=["b"]),
                TaskSpec(task_key="b", agent_name="b", depends_on=["a"]),
            ]
        )


def test_task_graph_order_ready_blocked_and_downstream() -> None:
    """Graph utilities order dependencies and find blocked/transitive tasks."""
    plan = _plan(
        TaskSpec(task_key="a", agent_name="a"),
        TaskSpec(task_key="b", agent_name="b"),
        TaskSpec(task_key="c", agent_name="c", depends_on=["a", "b"]),
        TaskSpec(task_key="d", agent_name="d", depends_on=["c"]),
    )
    assert topological_order(plan) == ["a", "b", "c", "d"]
    assert [task.task_key for task in ready_tasks(plan, {})] == ["a", "b"]
    states: dict[str, TaskExecutionState] = {
        "a": {"status": AgentRunStatus.FAILED},
        "b": {"status": AgentRunStatus.SUCCEEDED},
    }
    assert [task.task_key for task in blocked_tasks(plan, states)] == ["c"]
    assert affected_downstream(plan, {"a"}) == {"c", "d"}


class _TimingAgent(BaseAgent):
    """Short-lived agent recording actual concurrent start/end times."""

    def __init__(self, name: str, observations: dict[str, tuple[float, float]]) -> None:
        self.name = name
        self.description = "Timing test agent"
        self.observations = observations

    async def run(self, context: AgentContext) -> AgentResult:
        del context
        started = time.perf_counter()
        await asyncio.sleep(0.04)
        finished = time.perf_counter()
        self.observations[self.name] = (started, finished)
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data={"name": self.name},
        )


class _SlowAgent(BaseAgent):
    """Agent that exceeds the executor's configured timeout."""

    name = "slow"
    description = "Timeout test agent"

    async def run(self, context: AgentContext) -> AgentResult:
        del context
        await asyncio.sleep(0.2)
        return AgentResult(agent_name=self.name, status=AgentRunStatus.SUCCEEDED)


class _LimitedAgent(BaseAgent):
    """Agent that tracks concurrent calls for the semaphore test."""

    name = "limited"
    description = "Concurrency test agent"

    def __init__(self, counters: dict[str, int]) -> None:
        self.counters = counters

    async def run(self, context: AgentContext) -> AgentResult:
        del context
        self.counters["active"] += 1
        self.counters["peak"] = max(self.counters["peak"], self.counters["active"])
        await asyncio.sleep(0.02)
        self.counters["active"] -= 1
        return AgentResult(agent_name=self.name, status=AgentRunStatus.SUCCEEDED)


class _OptionalObserver(StubAgent):
    """Stub capturing the note delivered after an optional task failure."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.degraded_note: str | None = None

    async def run(self, context: AgentContext) -> AgentResult:
        self.degraded_note = context.degraded_input_note
        return await super().run(context)


class _BookingInput(BaseModel):
    reservation_id: str


class _BookingOutput(BaseModel):
    confirmed: bool


class _BookingTool(Tool[_BookingInput, _BookingOutput]):
    """Test tool tagged as a booking action."""

    name = "booking_tool"
    description = "Test booking operation"
    input_schema = _BookingInput
    output_schema = _BookingOutput
    provider = "test"
    approval_action = ApprovalActionType.BOOKING

    def __init__(self) -> None:
        self.calls = 0
        super().__init__(cache_ttl_s=0, retries=0)

    async def _run(self, payload: _BookingInput) -> _BookingOutput:
        self.calls += 1
        return _BookingOutput(confirmed=bool(payload.reservation_id))


class _BookingToolAgent(BaseAgent):
    """Call the tagged test tool from an otherwise ordinary task."""

    name = "booking-tool-agent"
    description = "Test agent that invokes a protected tool"

    async def run(self, context: AgentContext) -> AgentResult:
        await context.tool_registry.get("booking_tool").run(
            {"reservation_id": "candidate"}
        )
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
        )


@pytest.mark.asyncio
async def test_executor_parallel_dependencies_retry_skip_and_optional_failure() -> None:
    """Independent tasks overlap; dependencies wait; retries and skip rules hold."""
    observations: dict[str, tuple[float, float]] = {}
    agents = AgentRegistry()
    for name in ("first", "second", "dependent"):
        agents.register(_TimingAgent(name, observations))
    flaky = FlakyAgent("flaky", failures=1)
    failing = FailingAgent("broken", transient=True)
    optional = FailingAgent("optional", transient=True)
    observer = _OptionalObserver("observer")
    observer2 = _OptionalObserver("observer2")
    agents.register(flaky)
    agents.register(failing)
    agents.register(optional)
    agents.register(observer)
    agents.register(observer2)
    router, _ = _router()
    executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        InMemoryEventBus(),
        settings=Settings(
            use_fake_providers=True,
            agent_max_concurrency=2,
            agent_task_timeout_s=1,
            agent_task_retries=1,
            agent_task_retry_base_s=0,
        ),
    )
    plan = _plan(
        TaskSpec(task_key="first", agent_name="first"),
        TaskSpec(task_key="second", agent_name="second"),
        TaskSpec(task_key="dependent", agent_name="dependent", depends_on=["first"]),
        TaskSpec(task_key="flaky", agent_name="flaky"),
        TaskSpec(task_key="broken", agent_name="broken"),
        TaskSpec(task_key="skip", agent_name="dependent", depends_on=["broken"]),
        TaskSpec(task_key="skip-child", agent_name="dependent", depends_on=["skip"]),
        TaskSpec(
            task_key="optional-skip",
            agent_name="observer2",
            depends_on=["broken"],
            optional=True,
        ),
        TaskSpec(
            task_key="after-optional-skip",
            agent_name="observer2",
            depends_on=["optional-skip"],
        ),
        TaskSpec(task_key="optional", agent_name="optional", optional=True),
        TaskSpec(task_key="observe", agent_name="observer", depends_on=["optional"]),
    )
    result = await executor.execute(_state(plan))
    assert abs(observations["first"][0] - observations["second"][0]) < 0.03
    assert observations["dependent"][0] >= observations["first"][1]
    assert result["task_states"]["flaky"]["status"] == AgentRunStatus.SUCCEEDED
    assert result["task_states"]["flaky"]["attempts"] == 2
    assert result["task_states"]["broken"]["attempts"] == 2
    assert result["task_states"]["skip"]["status"] == AgentRunStatus.SKIPPED
    assert result["task_states"]["skip-child"]["status"] == AgentRunStatus.SKIPPED
    assert result["task_states"]["optional-skip"]["status"] == AgentRunStatus.SKIPPED
    assert (
        result["task_states"]["after-optional-skip"]["status"]
        == AgentRunStatus.SUCCEEDED
    )
    assert observer2.degraded_note is not None
    assert result["task_states"]["observe"]["status"] == AgentRunStatus.SUCCEEDED
    assert observer.degraded_note is not None


@pytest.mark.asyncio
async def test_executor_timeout_retry_and_concurrency_limit() -> None:
    """Timeouts are bounded retries and the semaphore caps active agents."""
    agents = AgentRegistry()
    slow = _SlowAgent()
    agents.register(slow)
    router, _ = _router()
    timeout_executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        InMemoryEventBus(),
        settings=Settings(
            use_fake_providers=True,
            agent_task_timeout_s=0.01,
            agent_task_retries=1,
            agent_task_retry_base_s=0,
        ),
    )
    timeout_result = await timeout_executor.execute(
        _state(_plan(TaskSpec(task_key="slow", agent_name="slow")))
    )
    assert timeout_result["task_states"]["slow"]["status"] == AgentRunStatus.FAILED
    assert timeout_result["task_states"]["slow"]["attempts"] == 2

    counters = {"active": 0, "peak": 0}
    limited_agents = AgentRegistry()
    limited_agents.register(_LimitedAgent(counters))
    limited_executor = WorkflowExecutor(
        limited_agents,
        router,
        ToolRegistry(),
        InMemoryEventBus(),
        settings=Settings(
            use_fake_providers=True,
            agent_max_concurrency=1,
            agent_task_timeout_s=1,
            agent_task_retries=0,
        ),
    )
    await limited_executor.execute(
        _state(
            _plan(
                TaskSpec(task_key="one", agent_name="limited"),
                TaskSpec(task_key="two", agent_name="limited"),
            )
        )
    )
    assert counters["peak"] == 1


@pytest.mark.asyncio
async def test_executor_skips_successful_persisted_tasks_on_resume() -> None:
    """Persisted success state bypasses the agent during crash recovery."""

    class SavedSuccess:
        async def load_succeeded(
            self,
            state: PlanningState,
            plan_tasks: list[TaskSpec],
        ) -> dict[str, TaskExecutionState]:
            del state, plan_tasks
            return {
                "done": {
                    "status": AgentRunStatus.SUCCEEDED,
                    "attempts": 1,
                    "result": AgentResult(
                        agent_name="done",
                        status=AgentRunStatus.SUCCEEDED,
                        data={"recovered": True},
                    ),
                }
            }

        async def save_transition(
            self,
            state: PlanningState,
            task: TaskSpec,
            task_state: TaskExecutionState,
            token_usage: dict[str, int | float] | None,
        ) -> None:
            del state, task, task_state, token_usage

    agent = StubAgent("done")
    agents = AgentRegistry()
    agents.register(agent)
    router, _ = _router()
    executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        InMemoryEventBus(),
        persistence=SavedSuccess(),
    )
    result = await executor.execute(
        _state(_plan(TaskSpec(task_key="done", agent_name="done")))
    )
    assert agent.calls == 0
    assert result["results"]["done"].data == {"recovered": True}


@pytest.mark.asyncio
async def test_approval_gate_blocks_booking_agent_until_approved() -> None:
    """A booking-tagged agent cannot run before an explicit approval."""
    agent = StubAgent("booking")
    agents = AgentRegistry()
    agents.register(agent)
    router, _ = _router()
    bus = InMemoryEventBus()
    gate = ApprovalGate(bus)
    executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        bus,
        approval_gate=gate,
        settings=Settings(
            use_fake_providers=True,
            agent_task_timeout_s=1,
            agent_task_retries=0,
        ),
    )
    plan = _plan(
        TaskSpec(
            task_key="booking",
            agent_name="booking",
            approval_action=ApprovalActionType.BOOKING,
        )
    )
    unguarded_executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        InMemoryEventBus(),
        settings=Settings(
            use_fake_providers=True,
            agent_task_timeout_s=1,
            agent_task_retries=0,
        ),
    )
    blocked = await unguarded_executor.execute(_state(plan))
    assert blocked["task_states"]["booking"]["status"] == AgentRunStatus.FAILED
    assert agent.calls == 0

    execution = asyncio.create_task(executor.execute(_state(plan)))
    for _ in range(100):
        approval = next(
            (
                event
                for event in bus.events
                if event.event_type == EventType.APPROVAL_NEEDED
            ),
            None,
        )
        if approval is not None:
            break
        await asyncio.sleep(0.001)
    assert approval is not None
    assert agent.calls == 0
    gate.resolve(UUID(str(approval.payload["approval_id"])), True)
    result = await execution
    assert result["task_states"]["booking"]["status"] == AgentRunStatus.SUCCEEDED
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_booking_tagged_tool_waits_for_explicit_approval() -> None:
    """A tool-level booking tag creates an approval request before its call."""
    tool = _BookingTool()
    tools = ToolRegistry()
    tools.register(tool)
    agents = AgentRegistry()
    agents.register(_BookingToolAgent())
    router, _ = _router()
    bus = InMemoryEventBus()
    gate = ApprovalGate(bus)
    executor = WorkflowExecutor(
        agents,
        router,
        tools,
        bus,
        approval_gate=gate,
        settings=Settings(
            use_fake_providers=True,
            agent_task_timeout_s=1,
            agent_task_retries=0,
        ),
    )
    execution = asyncio.create_task(
        executor.execute(
            _state(
                _plan(
                    TaskSpec(
                        task_key="booking-task",
                        agent_name="booking-tool-agent",
                    )
                )
            )
        )
    )
    for _ in range(100):
        approval = next(
            (
                event
                for event in bus.events
                if event.event_type == EventType.APPROVAL_NEEDED
            ),
            None,
        )
        if approval is not None:
            break
        await asyncio.sleep(0.001)
    assert approval is not None
    assert tool.calls == 0
    gate.resolve(UUID(str(approval.payload["approval_id"])), True)
    result = await execution
    assert result["task_states"]["booking-task"]["status"] == (AgentRunStatus.SUCCEEDED)
    assert tool.calls == 1


@pytest.mark.asyncio
async def test_supervisor_extracts_missing_fields_and_falls_back_after_repairs() -> (
    None
):
    """Typed extraction reports gaps and an invalid repaired plan falls back."""
    extraction = json.dumps(
        {
            "destination": None,
            "start_date": None,
            "end_date": None,
            "traveler_count": None,
            "budget": None,
        }
    )
    invalid_plan = json.dumps(
        {
            "tasks": [
                {"task_key": "duplicate", "agent_name": "destination"},
                {"task_key": "duplicate", "agent_name": "transport"},
            ]
        }
    )
    router, fake = _router([extraction, invalid_plan, invalid_plan, invalid_plan])
    agents = AgentRegistry()
    for name in ("destination", "transport", "itinerary", "budget", "validation"):
        agents.register(StubAgent(name))
    supervisor = SupervisorAgent(router, agents)
    requirements = await supervisor.extract_requirements("Plan a trip")
    assert requirements.missing_fields == [
        "destination",
        "start_date",
        "end_date",
        "traveler_count",
        "budget",
    ]
    assert len(supervisor.clarification_questions(requirements)) == 5
    plan = await supervisor.build_plan(requirements)
    assert len(fake.calls) == 4
    assert plan.tasks[2].depends_on == [
        "destination",
        "transport",
    ]
    assert plan.tasks[3].depends_on == ["transport"]
    assert plan.tasks[4].depends_on == ["itinerary", "budget"]


@pytest.mark.asyncio
async def test_supervisor_enforces_registered_domain_dependencies() -> None:
    """Model plans are normalized with known upstream domain dependencies."""
    plan_response = json.dumps(
        {
            "tasks": [
                {"task_key": "destination-task", "agent_name": "destination"},
                {"task_key": "transport-task", "agent_name": "transport"},
                {"task_key": "stay-task", "agent_name": "accommodation"},
                {"task_key": "weather-task", "agent_name": "weather"},
                {"task_key": "activity-task", "agent_name": "activity"},
                {"task_key": "local-task", "agent_name": "local_intelligence"},
                {"task_key": "itinerary-task", "agent_name": "itinerary"},
                {"task_key": "budget-task", "agent_name": "budget"},
                {"task_key": "validation-task", "agent_name": "validation"},
            ]
        }
    )
    router, _ = _router([plan_response])
    agents = AgentRegistry()
    for name in (
        "destination",
        "transport",
        "accommodation",
        "weather",
        "activity",
        "local_intelligence",
        "itinerary",
        "budget",
        "validation",
    ):
        agents.register(StubAgent(name))

    plan = await SupervisorAgent(router, agents).build_plan(TripRequirements())
    tasks = {task.agent_name: task for task in plan.tasks}

    assert set(tasks["itinerary"].depends_on) == {
        "destination-task",
        "transport-task",
        "stay-task",
        "weather-task",
        "activity-task",
        "local-task",
    }
    assert set(tasks["budget"].depends_on) == {
        "transport-task",
        "stay-task",
        "activity-task",
    }
    assert set(tasks["validation"].depends_on) == {"itinerary-task", "budget-task"}


@pytest.mark.asyncio
async def test_graph_interrupt_resume_and_event_order() -> None:
    """Missing requirements interrupt; answers resume a complete stub workflow."""
    requirements_json = json.dumps(
        {
            "destination": None,
            "start_date": None,
            "end_date": None,
            "traveler_count": None,
            "budget": None,
        }
    )
    plan_json = json.dumps(
        {
            "tasks": [
                {"task_key": "destination", "agent_name": "destination"},
                {
                    "task_key": "itinerary",
                    "agent_name": "itinerary",
                    "depends_on": ["destination"],
                },
            ]
        }
    )
    router, _ = _router([requirements_json, plan_json])
    agents = AgentRegistry()
    destination = StubAgent("destination")
    itinerary = StubAgent("itinerary")
    agents.register(destination)
    agents.register(itinerary)
    bus = InMemoryEventBus()
    supervisor = SupervisorAgent(router, agents)
    executor = WorkflowExecutor(
        agents,
        router,
        ToolRegistry(),
        bus,
        settings=Settings(
            use_fake_providers=True,
            agent_task_timeout_s=1,
            agent_task_retries=0,
        ),
    )
    graph = build_planning_graph(supervisor, executor, InMemorySaver())
    state = _state(_plan())
    state["requirements"] = None
    state["plan"] = None
    state["user_request"] = "Help me plan a trip"
    config = {"configurable": {"thread_id": str(state["run_id"])}}
    waiting = await graph.ainvoke(state, config)
    assert waiting["__interrupt__"]
    completed = await graph.ainvoke(
        Command(
            resume={
                "destination": "Rome",
                "start_date": date(2027, 5, 1).isoformat(),
                "end_date": date(2027, 5, 4).isoformat(),
                "traveler_count": 2,
                "budget": "1200",
            }
        ),
        config,
    )
    assert "__interrupt__" not in completed
    assert destination.calls == itinerary.calls == 1
    assert completed["task_states"]["itinerary"]["status"] == AgentRunStatus.SUCCEEDED
    assert completed["event_log"][0]["event_type"] == EventType.RUN_STARTED
    assert completed["event_log"][-1]["event_type"] == EventType.RUN_COMPLETED
    event_types = [event.event_type for event in bus.events]
    assert event_types[0] == EventType.RUN_STARTED
    assert EventType.CLARIFICATION_NEEDED in event_types
    assert event_types.index(EventType.TASK_STARTED) < event_types.index(
        EventType.TASK_SUCCEEDED
    )
    assert event_types[-1] == EventType.RUN_COMPLETED
