"""Typed shared input and output contracts for future planning agents."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.enums import (
    AgentRunStatus,
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


class SupervisorPlan(APIModel):
    """A structured plan of tasks for future orchestration."""

    tasks: list[TaskSpec] = Field(default_factory=list)
    rationale: str | None = None


class DestinationResearchOutput(APIModel):
    """Destination overview and research results."""

    destination: str
    overview: str
    highlights: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class TransportOption(APIModel):
    """A candidate transportation option."""

    mode: str
    description: str
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None
    duration_minutes: int | None = Field(default=None, ge=0)


class TransportOutput(APIModel):
    """Transportation options for a requested route."""

    options: list[TransportOption] = Field(default_factory=list)


class AccommodationOption(APIModel):
    """A candidate accommodation."""

    name: str
    description: str | None = None
    place_id: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    estimated_cost: Decimal | None = Field(default=None, ge=0)
    currency: str | None = None


class AccommodationOutput(APIModel):
    """Accommodation options for a destination and date range."""

    options: list[AccommodationOption] = Field(default_factory=list)


class WeatherDay(APIModel):
    """Weather summary for one calendar day."""

    date: date
    summary: str
    temperature_min: float | None = None
    temperature_max: float | None = None


class WeatherOutput(APIModel):
    """Forecast summary for the travel period."""

    destination: str
    days: list[WeatherDay] = Field(default_factory=list)


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


class ActivityOutput(APIModel):
    """Candidate activities matched to trip requirements."""

    activities: list[ActivityOption] = Field(default_factory=list)


class LocalIntelligenceOutput(APIModel):
    """Local practical information relevant to a trip."""

    destination: str
    tips: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    transport_notes: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


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
