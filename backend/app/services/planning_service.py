"""Planning use cases with service-owned async SQLAlchemy transactions."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from typing import Any, TypeAlias, TypeVar
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.repositories.agent_runs import AgentRunRepository
from app.db.repositories.approvals import ApprovalRequestRepository
from app.db.repositories.task_nodes import TaskNodeRepository
from app.db.repositories.trips import TripRepository
from app.db.repositories.versions import TripVersionRepository
from app.llm.router import LLMRouter
from app.models.enums import (
    AgentRunStatus,
    ApprovalStatus,
    TripStatus,
)
from app.models.execution import AgentRun
from app.models.trip import BudgetCategory, BudgetEntry, ItineraryDay, ItineraryItem
from app.orchestration.agent_base import AgentRegistry
from app.orchestration.approvals import ApprovalGate, ApprovalTicket
from app.orchestration.events import EventBus, InMemoryEventBus
from app.orchestration.executor import TaskPersistence, WorkflowExecutor
from app.orchestration.graph import build_planning_graph
from app.orchestration.state import PlanningState, TaskExecutionState
from app.orchestration.supervisor import SupervisorAgent
from app.schemas.agent_io import (
    AgentResult,
    BudgetReport,
    ItineraryPlan,
    SupervisorPlan,
    TaskSpec,
    TripRequirements,
    ValidationReport,
)
from app.tools.registry import ToolRegistry

ModelT = TypeVar("ModelT", bound=BaseModel)


class PlanningRunInfo(BaseModel):
    """Public service representation of a planning run's current state."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: UUID
    trip_id: UUID
    status: AgentRunStatus
    requirements: TripRequirements | None = None
    plan: SupervisorPlan | None = None
    task_states: dict[str, TaskExecutionState] = Field(default_factory=dict)
    clarification_questions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    interrupted: bool = False


class PlanningService(TaskPersistence):
    """Coordinate graph execution and persistence without sharing sessions."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        llm_router: LLMRouter,
        tool_registry: ToolRegistry,
        agents: AgentRegistry,
        checkpointer: object,
        *,
        event_bus: EventBus | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Bind application resources and create the approval gate."""
        self.session_factory = session_factory
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.agents = agents
        self.checkpointer = checkpointer
        self.event_bus = event_bus or InMemoryEventBus()
        self.settings = settings or get_settings()
        self._active_runs: dict[UUID, asyncio.Task[dict[str, Any]]] = {}
        self.approval_gate = ApprovalGate(
            self.event_bus,
            create_request=self._create_approval_request,
            load_decision=self._load_approval_decision,
        )

    async def start_planning(
        self,
        trip_id: UUID,
        user_request: str,
    ) -> PlanningRunInfo:
        """Create a root run and begin planning for an existing trip."""
        async with self.session_factory() as session:
            async with session.begin():
                trip = await TripRepository(session).get(trip_id)
                if trip is None:
                    raise ValueError(f"Trip does not exist: {trip_id}")
                trip.status = TripStatus.PLANNING
                run = await AgentRunRepository(session).create(
                    trip_id=trip_id,
                    agent_name="supervisor",
                    status=AgentRunStatus.RUNNING,
                    input={"user_request": user_request},
                    started_at=datetime.now(UTC),
                )
                run_id = run.id
        initial_state: PlanningState = {
            "trip_id": trip_id,
            "run_id": run_id,
            "user_request": user_request,
            "requirements": None,
            "plan": None,
            "task_states": {},
            "results": {},
            "clarification_questions": [],
            "approval_requests": {},
            "errors": [],
            "event_log": [],
        }
        return await self._invoke(run_id, initial_state)

    async def provide_clarification(
        self,
        run_id: UUID,
        answers: dict[str, object],
    ) -> PlanningRunInfo:
        """Resume a clarification interrupt with validated user answers."""
        graph = self._build_graph()
        state = await graph.ainvoke(
            Command[PlanningState](resume=answers),
            self._config(run_id),
        )
        return await self._finish_graph_call(run_id, state)

    async def resume_run(self, run_id: UUID) -> PlanningRunInfo:
        """Resume the saved graph, reconciling successful persisted tasks."""
        graph = self._build_graph()
        snapshot = await graph.aget_state(self._config(run_id))
        if snapshot.values:
            state = await graph.ainvoke(None, self._config(run_id))
            return await self._finish_graph_call(run_id, state)
        async with self.session_factory() as session:
            run = await AgentRunRepository(session).get(run_id)
            if run is None:
                raise ValueError(f"Planning run does not exist: {run_id}")
            user_request = str(run.input["user_request"])
            trip_id = run.trip_id
        initial_state: PlanningState = {
            "trip_id": trip_id,
            "run_id": run_id,
            "user_request": user_request,
            "requirements": None,
            "plan": None,
            "task_states": {},
            "results": {},
            "clarification_questions": [],
            "approval_requests": {},
            "errors": [],
            "event_log": [],
        }
        return await self._invoke(run_id, initial_state)

    async def cancel_run(self, run_id: UUID) -> None:
        """Cancel an active run and persist terminal failure status."""
        active = self._active_runs.get(run_id)
        if active is not None and not active.done():
            active.cancel()
        await self._set_run_status(run_id, AgentRunStatus.FAILED)

    async def get_run_status(self, run_id: UUID) -> PlanningRunInfo:
        """Return the persisted run state and current task statuses."""
        async with self.session_factory() as session:
            run = await AgentRunRepository(session).get(run_id)
            if run is None:
                raise ValueError(f"Planning run does not exist: {run_id}")
            output = run.output or {}
            state = output.get("state", {})
            if not isinstance(state, dict):
                state = {}
            persisted_tasks = await TaskNodeRepository(session).list_by_trip(
                run.trip_id
            )
            task_states: dict[str, TaskExecutionState] = {}
            for node in persisted_tasks:
                checkpoint = node.checkpoint or {}
                if checkpoint.get("run_id") != str(run_id):
                    continue
                original_key = checkpoint.get("task_key")
                task_state = checkpoint.get("state")
                if isinstance(original_key, str) and isinstance(task_state, dict):
                    task_states[original_key] = _task_state_from_json(task_state)
            requirements = _requirements(state.get("requirements"))
            plan = _plan(state.get("plan"))
            return PlanningRunInfo(
                run_id=run.id,
                trip_id=run.trip_id,
                status=run.status,
                requirements=requirements,
                plan=plan,
                task_states=task_states,
                clarification_questions=state.get("clarification_questions", []),
                errors=state.get("errors", []),
                interrupted=bool(output.get("interrupted", False)),
            )

    async def resolve_approval(self, approval_id: UUID, approved: bool) -> None:
        """Persist a user's decision and release an active approval wait."""
        target_status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        async with self.session_factory() as session:
            async with session.begin():
                request = await ApprovalRequestRepository(session).get(approval_id)
                if request is None:
                    raise ValueError(f"Approval request does not exist: {approval_id}")
                if request.status != ApprovalStatus.PENDING:
                    raise ValueError("Approval request is no longer pending")
                await ApprovalRequestRepository(session).decide(request, target_status)
        if self.approval_gate.is_waiting(approval_id):
            self.approval_gate.resolve(approval_id, approved)

    async def save_transition(
        self,
        state: PlanningState,
        task: TaskSpec,
        task_state: TaskExecutionState,
        token_usage: dict[str, int | float] | None,
    ) -> None:
        """Persist a task node and child agent-run transition in its own session."""
        run_id = state["run_id"]
        trip_id = state["trip_id"]
        key = _persistent_task_key(run_id, task.task_key)
        dependencies = [
            _persistent_task_key(run_id, dependency) for dependency in task.depends_on
        ]
        checkpoint: dict[str, object] = {
            "run_id": str(run_id),
            "task_key": task.task_key,
            "state": _json_value(task_state),
        }
        async with self.session_factory() as session:
            async with session.begin():
                await TaskNodeRepository(session).upsert(
                    trip_id=trip_id,
                    task_key=key,
                    agent_name=task.agent_name,
                    depends_on=dependencies,
                    priority=task.priority,
                    status=task_state["status"],
                    checkpoint=checkpoint,
                )
                child_runs = await session.scalars(
                    select(AgentRun).where(AgentRun.supervisor_run_id == run_id)
                )
                child = next(
                    (
                        item
                        for item in child_runs
                        if item.input.get("task_key") == task.task_key
                    ),
                    None,
                )
                started = task_state.get("started_at")
                finished = task_state.get("finished_at")
                duration = (
                    int((finished - started).total_seconds() * 1000)
                    if started is not None and finished is not None
                    else None
                )
                result = task_state.get("result")
                values: dict[str, object] = {
                    "status": task_state["status"],
                    "output": result.model_dump(mode="json") if result else None,
                    "error": task_state.get("error"),
                    "retry_count": max(0, int(task_state.get("attempts", 0)) - 1),
                    "started_at": started,
                    "finished_at": finished,
                    "duration_ms": duration,
                    "token_usage": token_usage,
                }
                if child is None:
                    await AgentRunRepository(session).create(
                        trip_id=trip_id,
                        supervisor_run_id=run_id,
                        agent_name=task.agent_name,
                        input={
                            "task_key": task.task_key,
                            "inputs": task.inputs,
                        },
                        **values,
                    )
                else:
                    await AgentRunRepository(session).update(child, values)

    async def load_succeeded(
        self,
        state: PlanningState,
        plan_tasks: list[TaskSpec],
    ) -> dict[str, TaskExecutionState]:
        """Restore successful task results to avoid re-running completed work."""
        restored: dict[str, TaskExecutionState] = {}
        async with self.session_factory() as session:
            repository = TaskNodeRepository(session)
            for task in plan_tasks:
                node = await repository.get_by_trip_and_key(
                    state["trip_id"],
                    _persistent_task_key(state["run_id"], task.task_key),
                )
                if node is None or node.checkpoint is None:
                    continue
                if node.checkpoint.get("run_id") != str(state["run_id"]):
                    continue
                saved = node.checkpoint.get("state")
                if not isinstance(saved, dict):
                    continue
                task_state = _task_state_from_json(saved)
                if task_state.get("status") == AgentRunStatus.SUCCEEDED:
                    restored[task.task_key] = task_state
        return restored

    async def _invoke(
        self,
        run_id: UUID,
        initial_state: PlanningState,
    ) -> PlanningRunInfo:
        """Run graph asynchronously so cancel_run can interrupt it."""
        graph = self._build_graph()
        task = asyncio.create_task(graph.ainvoke(initial_state, self._config(run_id)))
        self._active_runs[run_id] = task
        try:
            state = await task
            return await self._finish_graph_call(run_id, state)
        except asyncio.CancelledError:
            await self._set_run_status(run_id, AgentRunStatus.FAILED)
            raise
        except Exception:
            await self._set_run_status(run_id, AgentRunStatus.FAILED)
            raise
        finally:
            self._active_runs.pop(run_id, None)

    async def _finish_graph_call(
        self,
        run_id: UUID,
        state: dict[str, Any],
    ) -> PlanningRunInfo:
        """Persist the graph snapshot and set trip/run lifecycle statuses."""
        interrupted = bool(state.get("__interrupt__"))
        serializable_state = {
            key: value for key, value in state.items() if key != "__interrupt__"
        }
        await self._save_root_output(run_id, serializable_state, interrupted)
        if not interrupted:
            await self._persist_final_version(run_id, serializable_state)
            task_states = state.get("task_states", {})
            has_failure = any(
                task_state.get("status")
                in {AgentRunStatus.FAILED, AgentRunStatus.SKIPPED}
                for task_state in task_states.values()
            )
            status = AgentRunStatus.FAILED if has_failure else AgentRunStatus.SUCCEEDED
            await self._set_run_status(run_id, status)
        return await self.get_run_status(run_id)

    async def _persist_final_version(self, run_id: UUID, state: dict[str, Any]) -> None:
        """Persist the final typed budget, itinerary, and validation snapshot."""
        results = state.get("results", {})
        if not isinstance(results, dict):
            return
        itinerary = _result_model(results.get("itinerary"), ItineraryPlan)
        budget = _result_model(results.get("budget"), BudgetReport)
        validation = _result_model(results.get("validation"), ValidationReport)
        if itinerary is None and budget is None:
            return
        async with self.session_factory() as session:
            async with session.begin():
                root = await AgentRunRepository(session).get(run_id)
                if root is None:
                    raise ValueError(f"Planning run does not exist: {run_id}")
                if root.output and root.output.get("final_version_id"):
                    return
                version = await TripVersionRepository(session).create_next_version(
                    root.trip_id,
                    itinerary=itinerary.model_dump(mode="json") if itinerary else {},
                    budget_snapshot={
                        "budget": budget.model_dump(mode="json") if budget else {},
                        "validation": (
                            validation.model_dump(mode="json") if validation else {}
                        ),
                    },
                    created_by="system",
                )
                if budget:
                    for allocation in budget.allocations:
                        session.add(
                            BudgetCategory(
                                trip_version_id=version.id,
                                category=allocation.category,
                                allocated_amount=allocation.allocated_amount,
                            )
                        )
                        session.add(
                            BudgetEntry(
                                trip_version_id=version.id,
                                category=allocation.category,
                                estimated_amount=allocation.estimated_amount,
                            )
                        )
                if itinerary:
                    for day_plan in itinerary.days:
                        day = ItineraryDay(
                            trip_version_id=version.id,
                            day_number=day_plan.day_number,
                            date=day_plan.date,
                            summary=day_plan.summary,
                        )
                        session.add(day)
                        await session.flush()
                        for index, item in enumerate(day_plan.items):
                            session.add(
                                ItineraryItem(
                                    day_id=day.id,
                                    order_index=index,
                                    item_type=item.item_type,
                                    title=item.title,
                                    description=item.description,
                                    start_time=item.start_time,
                                    end_time=item.end_time,
                                    place_id=item.place_id,
                                    latitude=item.latitude,
                                    longitude=item.longitude,
                                    estimated_cost=item.estimated_cost,
                                    currency=item.currency,
                                    metadata_=item.metadata,
                                )
                            )
                root.output = {
                    **(root.output or {}),
                    "final_version_id": str(version.id),
                }

    def _build_graph(
        self,
    ) -> CompiledStateGraph[PlanningState, None, PlanningState, PlanningState]:
        """Create a graph with service-owned persistence hooks."""
        supervisor = SupervisorAgent(self.llm_router, self.agents)
        executor = WorkflowExecutor(
            self.agents,
            self.llm_router,
            self.tool_registry,
            self.event_bus,
            settings=self.settings,
            persistence=self,
            approval_gate=self.approval_gate,
        )
        return build_planning_graph(supervisor, executor, self.checkpointer)

    def _config(self, run_id: UUID) -> RunnableConfig:
        """Return the stable checkpoint thread config for one run."""
        return {"configurable": {"thread_id": str(run_id)}}

    async def _set_run_status(
        self,
        run_id: UUID,
        status: AgentRunStatus,
    ) -> None:
        """Update root run and trip lifecycle in one transaction."""
        async with self.session_factory() as session:
            async with session.begin():
                root = await AgentRunRepository(session).get(run_id)
                if root is None:
                    raise ValueError(f"Planning run does not exist: {run_id}")
                finished_at = (
                    datetime.now(UTC)
                    if status in {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}
                    else None
                )
                duration = (
                    int((finished_at - root.started_at).total_seconds() * 1000)
                    if finished_at is not None and root.started_at is not None
                    else None
                )
                await AgentRunRepository(session).update(
                    root,
                    {
                        "status": status,
                        "finished_at": finished_at,
                        "duration_ms": duration,
                    },
                )
                trip = await TripRepository(session).get(root.trip_id)
                if trip is not None:
                    trip_status = (
                        TripStatus.PLANNED
                        if status == AgentRunStatus.SUCCEEDED
                        else TripStatus.FAILED
                        if status == AgentRunStatus.FAILED
                        else TripStatus.PLANNING
                    )
                    trip.status = trip_status

    async def _save_root_output(
        self,
        run_id: UUID,
        state: dict[str, Any],
        interrupted: bool,
    ) -> None:
        """Persist a JSON-compatible graph snapshot on the root agent run."""
        async with self.session_factory() as session:
            async with session.begin():
                root = await AgentRunRepository(session).get(run_id)
                if root is None:
                    raise ValueError(f"Planning run does not exist: {run_id}")
                root.output = {
                    "state": _json_value(state),
                    "interrupted": interrupted,
                }

    async def _create_approval_request(
        self,
        ticket: ApprovalTicket,
        inputs: dict[str, object],
    ) -> ApprovalTicket:
        """Create or reuse a pending approval before the agent can execute."""
        async with self.session_factory() as session:
            async with session.begin():
                repository = ApprovalRequestRepository(session)
                existing = await repository.list(filters={"trip_id": ticket.trip_id})
                previous = next(
                    (
                        request
                        for request in existing
                        if request.payload.get("run_id") == str(ticket.run_id)
                        and request.payload.get("task_key") == ticket.task_key
                    ),
                    None,
                )
                if previous is not None:
                    return ticket.model_copy(
                        update={
                            "approval_id": previous.id,
                            "status": previous.status.value,
                        }
                    )
                request = await repository.create(
                    id=ticket.approval_id,
                    trip_id=ticket.trip_id,
                    action_type=ticket.action_type,
                    payload={
                        "run_id": str(ticket.run_id),
                        "task_key": ticket.task_key,
                        "inputs": _json_value(inputs),
                    },
                    status=ApprovalStatus.PENDING,
                )
                return ticket.model_copy(update={"approval_id": request.id})

    async def _load_approval_decision(self, approval_id: UUID) -> bool | None:
        """Load a persisted approval decision for in-flight or resumed runs."""
        async with self.session_factory() as session:
            request = await ApprovalRequestRepository(session).get(approval_id)
            if request is None or request.status == ApprovalStatus.PENDING:
                return None
            return request.status == ApprovalStatus.APPROVED


def _persistent_task_key(run_id: UUID, task_key: str) -> str:
    """Generate a collision-resistant run-scoped key within the DB column limit."""
    digest = hashlib.sha256(task_key.encode("utf-8")).hexdigest()[:20]
    return f"{run_id.hex}:{digest}"


JsonValue: TypeAlias = (
    str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]
)


def _json_value(value: object) -> JsonValue:
    """Convert typed state values to JSON-compatible primitives recursively."""
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"Unsupported JSON state value: {type(value).__name__}")


def _task_state_from_json(value: dict[str, Any]) -> TaskExecutionState:
    """Validate persisted task state and restore its result contract."""
    restored: TaskExecutionState = {}
    status = value.get("status")
    if isinstance(status, str):
        restored["status"] = AgentRunStatus(status)
    elif isinstance(status, AgentRunStatus):
        restored["status"] = status
    attempts = value.get("attempts")
    if isinstance(attempts, int):
        restored["attempts"] = attempts
    for timestamp_key in ("started_at", "finished_at"):
        timestamp = value.get(timestamp_key)
        if isinstance(timestamp, str):
            restored[timestamp_key] = datetime.fromisoformat(timestamp)
        elif isinstance(timestamp, datetime):
            restored[timestamp_key] = timestamp
    error = value.get("error")
    if isinstance(error, str) or error is None:
        restored["error"] = error
    result = value.get("result")
    if isinstance(result, dict):
        restored["result"] = AgentResult.model_validate(result)
    degraded_note = value.get("degraded_input_note")
    if isinstance(degraded_note, str) or degraded_note is None:
        restored["degraded_input_note"] = degraded_note
    return restored


def _requirements(value: object) -> TripRequirements | None:
    """Restore a requirements model from serialized graph state."""
    return TripRequirements.model_validate(value) if isinstance(value, dict) else None


def _plan(value: object) -> SupervisorPlan | None:
    """Restore a supervisor plan from serialized graph state."""
    return SupervisorPlan.model_validate(value) if isinstance(value, dict) else None


def _result_model(value: object, model: type[ModelT]) -> ModelT | None:
    """Validate an agent envelope's typed data when it is available."""
    if isinstance(value, dict):
        value = AgentResult.model_validate(value)
    if not isinstance(value, AgentResult) or value.data is None:
        return None
    return model.model_validate(value.data)
