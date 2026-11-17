"""Typed shared input and output contracts for future planning agents."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.enums import (
    AgentRunStatus,
    ApprovalActionType,
    BudgetCategoryType,
    ItineraryItemType,
    TravelerType,
)
from app.schemas.common import APIModel, normalize_currency


class TripRequirements(APIModel):
    """Structured requirements extracted from a travel request."""

    destination: str | None = None
    origin: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    traveler_count: int | None = Field(default=None, ge=1)
    traveler_type: TravelerType | None = None
    budget: Decimal | None = Field(default=None, gt=0)
    currency: str | None = None
    interests: list[str] = Field(default_factory=list)
    food: list[str] = Field(default_factory=list)
    accommodation: list[str] = Field(default_factory=list)
    accessibility: list[str] = Field(default_factory=list)
    travel_style: str | None = None
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> TripRequirements:
        """Ensure the requested end date does not precede the start date."""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

    @field_validator("currency")
    @classmethod
    def currency_is_iso_formatted(cls, value: str | None) -> str | None:
        """Normalize optional ISO currency code."""
        return normalize_currency(value) if value is not None else None


class TaskSpec(APIModel):
    """A task proposed for execution in a planning workflow."""

    task_key: str = Field(min_length=1, max_length=160)
    agent_name: str = Field(min_length=1, max_length=120)
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    priority: int = Field(default=0, ge=0)
    optional: bool = False
    approval_action: ApprovalActionType | None = None


class SupervisorPlan(APIModel):
    """A structured plan of tasks for future orchestration."""

    tasks: list[TaskSpec] = Field(default_factory=list)
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_task_graph(self) -> SupervisorPlan:
        """Reject duplicate keys, invalid references, self-links, and cycles."""
        from collections import Counter

        counts = Counter(task.task_key for task in self.tasks)
        duplicates = sorted(key for key, count in counts.items() if count > 1)
        task_keys = set(counts)
        missing = sorted(
            {
                dependency
                for task in self.tasks
                for dependency in task.depends_on
                if dependency not in task_keys
            }
        )
        self_dependencies = sorted(
            {task.task_key for task in self.tasks if task.task_key in task.depends_on}
        )

        dependencies = {
            task.task_key: [
                key
                for key in task.depends_on
                if key in task_keys and key != task.task_key
            ]
            for task in self.tasks
        }
        cycles = _find_dependency_cycles(dependencies)
        issues: list[str] = []
        if duplicates:
            issues.append(f"duplicate task_key(s): {duplicates}")
        if missing:
            issues.append(f"missing dependency key(s): {missing}")
        if self_dependencies:
            issues.append(f"self-dependency key(s): {self_dependencies}")
        if cycles:
            issues.append(f"cycle key(s): {cycles}")
        if issues:
            raise ValueError("Invalid task graph: " + "; ".join(issues))
        return self


def _find_dependency_cycles(dependencies: dict[str, list[str]]) -> list[list[str]]:
    """Return each detected dependency cycle as an ordered key path."""
    completed: set[str] = set()
    active: list[str] = []
    active_indexes: dict[str, int] = {}
    cycles: dict[tuple[str, ...], list[str]] = {}

    def visit(task_key: str) -> None:
        if task_key in completed:
            return
        active_indexes[task_key] = len(active)
        active.append(task_key)
        for dependency in dependencies[task_key]:
            if dependency in active_indexes:
                cycle = active[active_indexes[dependency] :] + [dependency]
                canonical = min(
                    tuple(cycle[index:-1] + cycle[:index])
                    for index in range(len(cycle) - 1)
                )
                cycles[canonical] = cycle
            elif dependency not in completed:
                visit(dependency)
        active.pop()
        active_indexes.pop(task_key)
        completed.add(task_key)

    for key in dependencies:
        visit(key)
    return [cycles[key] for key in sorted(cycles)]


class DestinationResearchOutput(APIModel):
    """Destination overview and research results."""

    destination: str
    overview: str
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    popular_attractions: list[dict[str, Any]] = Field(default_factory=list)
    hidden_gems: list[dict[str, Any]] = Field(default_factory=list)
    best_areas_to_stay: list[str] = Field(default_factory=list)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class TransportOption(APIModel):
    """A candidate transportation option."""

    mode: str
    description: str
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    distance_km: float | None = Field(default=None, ge=0)
    estimated_cost_min: Decimal | None = Field(default=None, ge=0)
    estimated_cost_max: Decimal | None = Field(default=None, ge=0)
    is_estimate: bool = True
    source: str = "heuristic"
    verified: bool = False
    verification_note: str | None = None


class TransportOutput(APIModel):
    """Transportation options for a requested route."""

    options: list[TransportOption] = Field(default_factory=list)
    recommended_mode: str | None = None
    local_options: list[TransportOption] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class AccommodationOption(APIModel):
    """A candidate accommodation."""

    name: str
    description: str | None = None
    place_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    estimated_cost_min: Decimal | None = Field(default=None, ge=0)
    estimated_cost_max: Decimal | None = Field(default=None, ge=0)
    price_per_night_cap: Decimal | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    opening_hours: list[str] | None = None
    amenities: list[str] = Field(default_factory=list)
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    verified: bool = False
    verification_note: str | None = None
    source: str = "heuristic"
    is_estimate: bool = True


class AccommodationOutput(APIModel):
    """Accommodation options for a destination and date range."""

    options: list[AccommodationOption] = Field(default_factory=list)
    per_night_cap: Decimal | None = Field(default=None, ge=0)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class WeatherDay(APIModel):
    """Weather summary for one calendar day."""

    date: date
    summary: str
    temperature_min: float | None = None
    temperature_max: float | None = None
    precipitation_probability: float | None = Field(default=None, ge=0, le=100)
    outdoor_suitability: float = Field(default=0, ge=0, le=1)
    risk_flags: list[str] = Field(default_factory=list)
    is_estimate: bool = False


class WeatherOutput(APIModel):
    """Forecast summary for the travel period."""

    destination: str
    days: list[WeatherDay] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    packing_suggestions: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class ActivityOption(APIModel):
    """A candidate activity or point of interest."""

    title: str
    description: str | None = None
    place_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)
    category: str = "activity"
    best_time_of_day: str | None = None
    indoor: bool | None = None
    opening_hours: list[str] | None = None
    weather_sensitive: bool = False
    verified: bool = False
    verification_note: str | None = None
    source: str = "llm"
    is_estimate: bool = True


class ActivityOutput(APIModel):
    """Candidate activities matched to trip requirements."""

    activities: list[ActivityOption] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)


class LocalIntelligenceOutput(APIModel):
    """Local practical information relevant to a trip."""

    destination: str
    tips: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    transport_notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0, ge=0, le=1)
    verified: bool = False
    verification_note: str | None = None
    cultural_etiquette: list[str] = Field(default_factory=list)
    scams_to_avoid: list[str] = Field(default_factory=list)
    payment_currency: list[str] = Field(default_factory=list)
    emergency_numbers: dict[str, str] = Field(default_factory=dict)
    language_basics: list[str] = Field(default_factory=list)
    seasonal_notes: list[str] = Field(default_factory=list)


class BudgetAllocation(APIModel):
    """Budget allocation and estimate for one category."""

    category: BudgetCategoryType
    allocated_amount: Decimal = Field(ge=0)
    estimated_amount: Decimal = Field(ge=0)


class BudgetReport(APIModel):
    """Category allocations and total budget comparison."""

    currency: str
    total_budget: Decimal = Field(gt=0)
    allocations: list[BudgetAllocation] = Field(default_factory=list)
    total_allocated: Decimal = Field(ge=0)
    total_estimated: Decimal = Field(ge=0)
    over_budget: bool
    suggestions: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def currency_is_iso_formatted(cls, value: str) -> str:
        """Normalize ISO currency code."""
        return normalize_currency(value)


class ItineraryItemPlan(APIModel):
    """A typed item to place within an itinerary day."""

    id: UUID | None = None
    item_type: ItineraryItemType
    title: str
    description: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    place_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None


class ItineraryDayPlan(APIModel):
    """A dated itinerary day and ordered activities."""

    day_number: int = Field(ge=1)
    date: date
    summary: str | None = None
    items: list[ItineraryItemPlan] = Field(default_factory=list)


class ItineraryPlan(APIModel):
    """A complete itinerary proposal."""

    days: list[ItineraryDayPlan] = Field(default_factory=list)


class ValidationIssue(APIModel):
    """A specific itinerary or budget validation issue."""

    severity: Literal["info", "warning", "error", "critical"]
    type: Literal["budget", "schedule", "distance", "timing", "consistency"]
    message: str
    affected_item_ids: list[UUID] = Field(default_factory=list)


class ValidationReport(APIModel):
    """Validation results for a proposed itinerary."""

    issues: list[ValidationIssue] = Field(default_factory=list)
    passed: bool


class AgentResult(APIModel):
    """Standard envelope for the eventual result of an agent invocation."""

    agent_name: str
    status: AgentRunStatus
    data: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    duration_ms: int | None = Field(default=None, ge=0)
    token_usage: dict[str, int | float] | None = None
