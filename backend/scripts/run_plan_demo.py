"""Run a no-network orchestration workflow with fake LLM and stub agents."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import cast
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import TravelerType
from app.orchestration.agent_base import AgentContext, AgentRegistry
from app.orchestration.events import InMemoryEventBus
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.graph import build_planning_graph
from app.orchestration.state import PlanningState, TaskExecutionState
from app.orchestration.supervisor import SupervisorAgent
from app.orchestration.testing import StubAgent
from app.schemas.agent_io import AgentResult, TripRequirements
from app.tools.registry import ToolRegistry


class DelayedStubAgent(StubAgent):
    """Demo stub that makes concurrent task execution visible."""

    async def run(self, context: AgentContext) -> AgentResult:
        """Pause briefly before returning the deterministic stub result."""
        await asyncio.sleep(0.5)
        return await super().run(context)


async def run_demo() -> None:
    """Execute a complete stub workflow and print events plus final state."""
    settings = Settings(
        use_fake_providers=True,
        agent_max_concurrency=3,
        agent_task_timeout_s=5,
        agent_task_retries=1,
        agent_task_retry_base_s=0,
    )
    requirements = TripRequirements(
        destination="Rome",
        origin="London",
        start_date="2027-05-01",
        end_date="2027-05-04",
        traveler_count=2,
        traveler_type=TravelerType.COUPLE,
        budget=1200,
        currency="EUR",
    )
    model_response = [
        requirements.model_dump_json(),
        json.dumps(
            {
                "tasks": [
                    {"task_key": "destination", "agent_name": "destination"},
                    {"task_key": "transport", "agent_name": "transport"},
                    {"task_key": "weather", "agent_name": "weather"},
                    {
                        "task_key": "itinerary",
                        "agent_name": "itinerary",
                        "depends_on": ["destination", "transport", "weather"],
                    },
                ],
                "rationale": "Demonstration plan with independent research tasks",
            }
        ),
    ]
    llm_router = LLMRouter(
        {"fake": FakeLLMClient(model_response)},
        primary_provider="fake",
        settings=settings,
    )
    agents = AgentRegistry()
    for name in ("destination", "transport", "weather", "itinerary"):
        agents.register(DelayedStubAgent(name))
    event_bus = InMemoryEventBus()
    supervisor = SupervisorAgent(llm_router, agents)
    executor = WorkflowExecutor(
        agents,
        llm_router,
        ToolRegistry(),
        event_bus,
        settings=settings,
    )
    graph = build_planning_graph(supervisor, executor, InMemorySaver())
    run_id = uuid4()
    state: PlanningState = {
        "trip_id": uuid4(),
        "run_id": run_id,
        "user_request": "Plan a four-day Rome trip for two people under EUR 1,200.",
        "requirements": None,
        "plan": None,
        "task_states": {},
        "results": {},
        "clarification_questions": [],
        "approval_requests": {},
        "errors": [],
        "event_log": [],
    }
    run_started = asyncio.get_running_loop().time()
    result = cast(
        PlanningState,
        await graph.ainvoke(
            state,
            {"configurable": {"thread_id": str(run_id)}},
        ),
    )
    wall_clock_s = asyncio.get_running_loop().time() - run_started
    run_started_event = next(
        event for event in event_bus.events if event.event_type == "run_started"
    )
    print("EVENT STREAM")
    for event in event_bus.events:
        offset_ms = (
            event.timestamp - run_started_event.timestamp
        ).total_seconds() * 1000
        print(
            f"{offset_ms:7.1f} ms  {event.event_type.value:<18} "
            f"{event.task_key or '-'}"
        )
    _print_parallelism_summary(result, wall_clock_s)
    print("FINAL STATE")
    print(json.dumps(result, indent=2, default=_json_default))


def _print_parallelism_summary(result: PlanningState, wall_clock_s: float) -> None:
    """Print overlap and duration measurements for the independent tasks."""
    independent_tasks = ("destination", "transport", "weather")
    task_states = result["task_states"]
    print("PARALLELISM CHECK")
    for index, first_key in enumerate(independent_tasks):
        for second_key in independent_tasks[index + 1 :]:
            first = task_states[first_key]
            second = task_states[second_key]
            print(
                f"{first_key} + {second_key}: "
                f"{_intervals_overlap(first, second)}"
            )
    durations = [
        _duration_seconds(task_states[task_key])
        for task_key in (*independent_tasks, "itinerary")
    ]
    print(
        f"wall-clock: {wall_clock_s:.3f}s; "
        f"sum task durations: {sum(durations):.3f}s"
    )


def _intervals_overlap(
    first: TaskExecutionState,
    second: TaskExecutionState,
) -> str:
    """Return whether two completed task intervals overlap."""
    first_start, first_finish = _task_interval(first)
    second_start, second_finish = _task_interval(second)
    return (
        "overlap"
        if first_start < second_finish and second_start < first_finish
        else "no overlap"
    )


def _task_interval(task_state: TaskExecutionState) -> tuple[datetime, datetime]:
    """Extract a completed task interval from the executor state."""
    started_at = task_state.get("started_at")
    finished_at = task_state.get("finished_at")
    if not isinstance(started_at, datetime) or not isinstance(finished_at, datetime):
        raise ValueError("Demo task did not record a complete execution interval")
    return started_at, finished_at


def _duration_seconds(task_state: TaskExecutionState) -> float:
    """Return one task's recorded execution duration in seconds."""
    started_at, finished_at = _task_interval(task_state)
    return (finished_at - started_at).total_seconds()


def _json_default(value: object) -> object:
    """Serialize Pydantic models and UUIDs in the printed demo state."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


if __name__ == "__main__":
    asyncio.run(run_demo())
