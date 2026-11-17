"""Shared state contracts and merge reducers for planning graph execution."""

from __future__ import annotations

import operator
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, TypedDict, TypeVar
from uuid import UUID

from app.models.enums import AgentRunStatus
from app.schemas.agent_io import (
    AgentResult,
    BudgetReport,
    ItineraryPlan,
    SupervisorPlan,
    TripRequirements,
    ValidationReport,
)

ValueT = TypeVar("ValueT")


class TaskExecutionState(TypedDict, total=False):
    """Checkpointable state for one task in a planning run."""

    status: AgentRunStatus
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    result: AgentResult | None
    degraded_input_note: str | None
    duration_ms: int | None
    token_usage: dict[str, int | float] | None


def merge_dicts(
    left: Mapping[str, ValueT] | None,
    right: Mapping[str, ValueT] | None,
) -> dict[str, ValueT]:
    """Merge independent task-keyed updates without dropping prior entries."""
    return {**(left or {}), **(right or {})}


class PlanningState(TypedDict, total=False):
    """Shared LangGraph state; mapping/list reducers merge fan-out updates."""

    trip_id: UUID
    user_request: str
    requirements: TripRequirements | None
    plan: SupervisorPlan | None
    task_states: Annotated[dict[str, TaskExecutionState], merge_dicts]
    results: Annotated[dict[str, AgentResult], merge_dicts]
    budget_report: BudgetReport | None
    itinerary_plan: ItineraryPlan | None
    validation_report: ValidationReport | None
    clarification_questions: list[str]
    approval_requests: Annotated[dict[str, UUID], merge_dicts]
    errors: list[str]
    run_id: UUID
    event_log: Annotated[list[dict[str, object]], operator.add]
