"""Concurrent dependency-aware task execution with bounded recovery."""

from __future__ import annotations

import asyncio
import hashlib
import random
import time
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
import structlog

from app.core.config import Settings, get_settings
from app.core.exceptions import ExternalServiceError
from app.llm.router import LLMRouter
from app.llm.usage import TokenUsageTracker, track_usage
from app.models.enums import AgentRunStatus, ApprovalActionType
from app.orchestration.agent_base import (
    AgentContext,
    AgentRegistry,
    TransientAgentError,
)
from app.orchestration.approvals import ApprovalGate
from app.orchestration.events import EventBus, EventType, WorkflowEvent
from app.orchestration.state import PlanningState, TaskExecutionState
from app.orchestration.task_graph import blocked_tasks, ready_tasks
from app.schemas.agent_io import AgentResult, TaskSpec
from app.tools.approval import tool_approval_context
from app.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


class TaskPersistence(Protocol):
    """Persistence callbacks that keep session ownership outside the executor."""

    async def save_transition(
        self,
        state: PlanningState,
        task: TaskSpec,
        task_state: TaskExecutionState,
        token_usage: dict[str, int | float] | None,
    ) -> None:
        """Persist a task status and associated run record."""

    async def load_succeeded(
        self,
        state: PlanningState,
        plan_tasks: list[TaskSpec],
    ) -> dict[str, TaskExecutionState]:
        """Load successful task state that survived a prior process."""


class WorkflowExecutor:
    """Run ready tasks concurrently without sharing database sessions."""

    def __init__(
        self,
        agents: AgentRegistry,
        llm_router: LLMRouter,
        tool_registry: ToolRegistry,
        event_bus: EventBus,
        *,
        settings: Settings | None = None,
        persistence: TaskPersistence | None = None,
        approval_gate: ApprovalGate | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        """Configure the scheduler and its bounded execution controls."""
        self.agents = agents
        self.llm_router = llm_router
        self.tool_registry = tool_registry
        self.event_bus = event_bus
        self.settings = settings or get_settings()
        self.persistence = persistence
        self.approval_gate = approval_gate
        self._random = random_source or random.Random()

    async def execute(self, state: PlanningState) -> PlanningState:
        """Run the task plan to completion, skipping blocked dependents only."""
        plan = state.get("plan")
        if plan is None:
            raise ValueError("Planning state has no supervisor plan")
        task_states = dict(state.get("task_states", {}))
        recovered = (
            await self.persistence.load_succeeded(state, plan.tasks)
            if self.persistence is not None
            else {}
        )
        for key, recovered_state in recovered.items():
            if recovered_state.get("status") == AgentRunStatus.SUCCEEDED:
                task_states[key] = recovered_state
        for task in plan.tasks:
            task_states.setdefault(
                task.task_key,
                {
                    "status": AgentRunStatus.PENDING,
                    "attempts": 0,
                    "started_at": None,
                    "finished_at": None,
                    "error": None,
                    "result": None,
                    "degraded_input_note": None,
                },
            )
        results = dict(state.get("results", {}))
        for task_key, task_state in task_states.items():
            result = task_state.get("result")
            if task_state.get("status") == AgentRunStatus.SUCCEEDED and result:
                results[task_key] = result

        semaphore = asyncio.Semaphore(self.settings.agent_max_concurrency)
        event_log = list(state.get("event_log", []))
        working_state: PlanningState = {
            **state,
            "task_states": task_states,
            "results": results,
        }

        while True:
            skipped = blocked_tasks(plan, task_states)
            for task in skipped:
                skipped_state: TaskExecutionState = {
                    **task_states[task.task_key],
                    "status": AgentRunStatus.SKIPPED,
                    "finished_at": datetime.now(UTC),
                    "error": "A required dependency failed",
                }
                await self._set_state(working_state, task, skipped_state, None)
                task_states[task.task_key] = skipped_state
                event_log.append(
                    await self._emit(
                        working_state, EventType.TASK_SKIPPED, task.task_key
                    )
                )
            if skipped:
                continue
            candidates = ready_tasks(plan, task_states)
            if not candidates:
                if all(
                    value.get("status")
                    in {
                        AgentRunStatus.SUCCEEDED,
                        AgentRunStatus.FAILED,
                        AgentRunStatus.SKIPPED,
                    }
                    for value in task_states.values()
                ):
                    break
                raise RuntimeError("Task scheduler reached a non-terminal deadlock")
            results_batch = await asyncio.gather(
                *(
                    self._run_task(
                        working_state,
                        task,
                        task_states[task.task_key],
                        task_states,
                        results,
                        semaphore,
                    )
                    for task in candidates
                )
            )
            for task_key, task_state, result, task_events in results_batch:
                task_states[task_key] = task_state
                if result is not None:
                    results[task_key] = result
                event_log.extend(task_events)
            working_state = {
                **working_state,
                "task_states": task_states,
                "results": results,
            }

        errors = list(state.get("errors", []))
        errors.extend(
            f"{key}: {value['error']}"
            for key, value in task_states.items()
            if value.get("status") == AgentRunStatus.FAILED
            and value.get("error") is not None
        )
        return {
            **state,
            "task_states": task_states,
            "results": results,
            "approval_requests": {
                **state.get("approval_requests", {}),
                **(
                    self.approval_gate.requests_for_run(state["run_id"])
                    if self.approval_gate is not None
                    else {}
                ),
            },
            "errors": errors,
            "event_log": event_log,
        }

    async def _run_task(
        self,
        state: PlanningState,
        task: TaskSpec,
        prior_state: TaskExecutionState,
        task_states: dict[str, TaskExecutionState],
        results: dict[str, AgentResult],
        semaphore: asyncio.Semaphore,
    ) -> tuple[
        str,
        TaskExecutionState,
        AgentResult | None,
        list[dict[str, object]],
    ]:
        """Run one task, including approval, retry, timeout, and event updates."""
        current: TaskExecutionState = {}
        current.update(prior_state)
        events: list[dict[str, object]] = []
        token_usage = TokenUsageTracker()
        async with semaphore:
            if task.approval_action is not None:
                approval_error: Exception | None = None
                approved = False
                if self.approval_gate is None:
                    approval_error = RuntimeError("Approval gate is not configured")
                else:
                    try:
                        approved = await self.approval_gate.wait_for_approval(
                            task,
                            trip_id=state["trip_id"],
                            run_id=state["run_id"],
                            inputs=task.inputs,
                        )
                    except Exception as error:
                        approval_error = error
                if approval_error is not None or not approved:
                    current["status"] = AgentRunStatus.FAILED
                    current["finished_at"] = datetime.now(UTC)
                    current["error"] = (
                        _safe_error(approval_error)
                        if approval_error is not None
                        else "Approval was rejected"
                    )
                    logger.warning(
                        "agent_task_blocked_by_approval",
                        task_key=task.task_key,
                        error_type=(
                            type(approval_error).__name__
                            if approval_error is not None
                            else "ApprovalRejected"
                        ),
                    )
                    await self._set_state(state, task, current, None)
                    events.append(
                        await self._emit(state, EventType.TASK_FAILED, task.task_key)
                    )
                    return task.task_key, current, None, events

            start = datetime.now(UTC)
            started_clock = time.perf_counter()
            current["status"] = AgentRunStatus.RUNNING
            current["started_at"] = current.get("started_at") or start
            current["finished_at"] = None
            current["error"] = None
            await self._set_state(state, task, current, None)
            events.append(
                await self._emit(state, EventType.TASK_STARTED, task.task_key)
            )
            attempt_limit = self.settings.agent_task_retries + 1
            last_error: Exception | None = None
            for attempt in range(1, attempt_limit + 1):
                current["attempts"] = (current.get("attempts") or 0) + 1
                try:
                    upstream = {
                        key: results[key] for key in task.depends_on if key in results
                    }
                    degraded = self._degraded_note(task, task_states)
                    agent_context = AgentContext(
                        state=dict(state),
                        task=task,
                        upstream_results=upstream,
                        llm_router=self.llm_router,
                        tool_registry=self.tool_registry,
                        logger=structlog.get_logger("travelmind.agent").bind(
                            agent_name=task.agent_name,
                            task_key=task.task_key,
                            run_id=str(state["run_id"]),
                        ),
                        deadline=datetime.now(UTC)
                        + timedelta(seconds=self.settings.agent_task_timeout_s),
                        degraded_input_note=degraded,
                    )

                    async def approve_tool(
                        action: ApprovalActionType,
                        tool_name: str,
                        inputs: dict[str, object],
                    ) -> bool:
                        if self.approval_gate is None:
                            return False
                        tool_task = task.model_copy(
                            update={
                                "task_key": _tool_approval_key(
                                    task.task_key,
                                    tool_name,
                                ),
                                "approval_action": action,
                                "inputs": {"tool_name": tool_name, **inputs},
                            }
                        )
                        return await self.approval_gate.wait_for_approval(
                            tool_task,
                            trip_id=state["trip_id"],
                            run_id=state["run_id"],
                            inputs=tool_task.inputs,
                        )

                    approved_actions = (
                        {task.approval_action}
                        if task.approval_action is not None
                        else set()
                    )
                    with tool_approval_context(approve_tool, approved_actions):
                        with track_usage(token_usage):
                            async with asyncio.timeout(
                                self.settings.agent_task_timeout_s
                            ):
                                result = await self.agents.get(task.agent_name).run(
                                    agent_context
                                )
                    if result.status != AgentRunStatus.SUCCEEDED:
                        raise RuntimeError("Agent returned a non-success status")
                    current["status"] = AgentRunStatus.SUCCEEDED
                    current["finished_at"] = datetime.now(UTC)
                    duration_ms = round((time.perf_counter() - started_clock) * 1000)
                    current["duration_ms"] = duration_ms
                    usage = token_usage.as_dict()
                    current["token_usage"] = usage
                    current["error"] = None
                    if result.duration_ms is None or result.token_usage is None:
                        result = result.model_copy(
                            update={
                                "duration_ms": (
                                    result.duration_ms
                                    if result.duration_ms is not None
                                    else duration_ms
                                ),
                                "token_usage": (
                                    result.token_usage
                                    if result.token_usage is not None
                                    else usage
                                ),
                            }
                        )
                    current["result"] = result
                    current["degraded_input_note"] = degraded
                    await self._set_state(state, task, current, token_usage.as_dict())
                    events.append(
                        await self._emit(state, EventType.TASK_SUCCEEDED, task.task_key)
                    )
                    return task.task_key, current, result, events
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    last_error = error
                    retryable = _is_transient(error)
                    if not retryable or attempt == attempt_limit:
                        break
                    current["status"] = AgentRunStatus.RETRYING
                    current["error"] = _safe_error(error)
                    await self._set_state(state, task, current, token_usage.as_dict())
                    events.append(
                        await self._emit(
                            state,
                            EventType.TASK_RETRYING,
                            task.task_key,
                            {"attempt": attempt},
                        )
                    )
                    delay = self.settings.agent_task_retry_base_s * (2 ** (attempt - 1))
                    await asyncio.sleep(
                        self._random.uniform(delay * 0.75, delay * 1.25)
                    )
            current["status"] = AgentRunStatus.FAILED
            current["finished_at"] = datetime.now(UTC)
            current["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
            current["token_usage"] = token_usage.as_dict()
            current["error"] = _safe_error(
                last_error or RuntimeError("Unknown task failure")
            )
            logger.warning(
                "agent_task_failed",
                task_key=task.task_key,
                agent_name=task.agent_name,
                error_type=type(last_error).__name__ if last_error else "Unknown",
                attempts=current["attempts"],
            )
            await self._set_state(state, task, current, token_usage.as_dict())
            events.append(await self._emit(state, EventType.TASK_FAILED, task.task_key))
            return task.task_key, current, None, events

    async def _set_state(
        self,
        state: PlanningState,
        task: TaskSpec,
        task_state: TaskExecutionState,
        token_usage: dict[str, int | float] | None,
    ) -> None:
        """Persist a transition before exposing it as completed."""
        if self.persistence is not None:
            await self.persistence.save_transition(state, task, task_state, token_usage)

    async def _emit(
        self,
        state: PlanningState,
        event_type: EventType,
        task_key: str,
        payload: dict[str, str | int | float | bool | None] | None = None,
    ) -> dict[str, object]:
        """Publish and serialize one workflow event for graph state."""
        event = WorkflowEvent(
            event_type=event_type,
            trip_id=state["trip_id"],
            run_id=state["run_id"],
            task_key=task_key,
            payload=payload or {},
        )
        await self.event_bus.publish(event)
        return event.model_dump(mode="json")

    @staticmethod
    def _degraded_note(
        task: TaskSpec,
        task_states: dict[str, TaskExecutionState],
    ) -> str | None:
        """Explain optional upstream failures to a downstream agent."""
        degraded_dependencies = [
            dependency
            for dependency in task.depends_on
            if task_states.get(dependency, {}).get("status")
            in {AgentRunStatus.FAILED, AgentRunStatus.SKIPPED}
        ]
        if not degraded_dependencies:
            return None
        return (
            "Optional upstream task(s) failed; continue with available information: "
            + ", ".join(sorted(degraded_dependencies))
        )


def _is_transient(error: Exception) -> bool:
    """Classify only explicitly transient transport and timeout failures."""
    if isinstance(
        error,
        (
            TransientAgentError,
            TimeoutError,
            ExternalServiceError,
            httpx.TimeoutException,
            httpx.NetworkError,
        ),
    ):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


def _safe_error(error: Exception) -> str:
    """Return a non-sensitive error summary suitable for persisted state."""
    if isinstance(error, TimeoutError):
        return "Task timed out"
    return f"Task failed ({type(error).__name__})"


def _tool_approval_key(task_key: str, tool_name: str) -> str:
    """Create a stable approval task key within the TaskSpec storage limit."""
    digest = hashlib.sha256(f"{task_key}:{tool_name}".encode()).hexdigest()[:40]
    return f"{task_key[:80]}:{digest}"
