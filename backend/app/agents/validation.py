"""Deterministic itinerary and budget validation."""

from __future__ import annotations

from datetime import time
from decimal import Decimal

from app.agents.base_research import ResearchAgent
from app.agents.itinerary import distance_km
from app.models.enums import AgentRunStatus, ItineraryItemType
from app.orchestration.agent_base import AgentContext
from app.schemas.agent_io import (
    AgentResult,
    BudgetReport,
    ItineraryDayPlan,
    ItineraryPlan,
    TripRequirements,
    ValidationIssue,
    ValidationReport,
    WeatherOutput,
)


def validate_itinerary(
    plan: ItineraryPlan,
    requirements: TripRequirements,
    budget: BudgetReport | None = None,
    weather: WeatherOutput | None = None,
) -> ValidationReport:
    """Run all deterministic Phase 6 checks and return a typed report."""
    issues: list[ValidationIssue] = []
    expected_days = (
        (requirements.end_date - requirements.start_date).days + 1
        if requirements.start_date and requirements.end_date
        else len(plan.days)
    )
    if len(plan.days) != expected_days:
        issues.append(
            ValidationIssue(
                severity="error",
                type="consistency",
                message=(
                    f"Expected {expected_days} itinerary days, found {len(plan.days)}."
                ),
                suggested_fix="Regenerate the missing itinerary days.",
                auto_fixable=True,
            )
        )
    if budget and budget.total_estimated > budget.total_budget:
        issues.append(
            ValidationIssue(
                severity="error",
                type="budget",
                message="Estimated itinerary cost exceeds the trip budget.",
                suggested_fix="Apply the cheapest feasible budget alternatives.",
                auto_fixable=True,
            )
        )
    seen_places: set[str] = set()
    for day in plan.days:
        _validate_day(day, issues)
        meals = {
            item.title.casefold()
            for item in day.items
            if item.item_type == ItineraryItemType.MEAL
        }
        if not {"breakfast", "lunch", "dinner"} <= meals:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    type="schedule",
                    message=f"Day {day.day_number} is missing one or more meal slots.",
                    suggested_fix="Add missing meal slots.",
                    auto_fixable=True,
                )
            )
        for item in day.items:
            if item.place_id and item.place_id in seen_places:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        type="consistency",
                        message=f"Place {item.title} is duplicated across days.",
                        suggested_fix="Remove the duplicate stop.",
                        auto_fixable=True,
                    )
                )
            if item.place_id:
                seen_places.add(item.place_id)
            if not item.verified and not item.tentative:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        type="consistency",
                        message=(
                            f"Unverified place {item.title} is not marked tentative."
                        ),
                        suggested_fix=(
                            "Mark the item tentative or replace it with a grounded "
                            "place."
                        ),
                        auto_fixable=True,
                    )
                )
            if requirements.accessibility and item.metadata.get("accessible") is False:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        type="consistency",
                        message=(
                            f"{item.title} does not meet accessibility requirements."
                        ),
                        suggested_fix="Replace it with an accessible alternative.",
                        auto_fixable=False,
                    )
                )
        if weather:
            weather_day = next(
                (entry for entry in weather.days if entry.date == day.date),
                None,
            )
            if (
                weather_day
                and weather_day.outdoor_suitability < 0.5
                and any(item.weather_sensitive for item in day.items)
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        type="timing",
                        message=(
                            "Outdoor activity scheduled on a weather-risk day "
                            f"{day.date}."
                        ),
                        suggested_fix="Move the activity to a safer weather day.",
                        auto_fixable=True,
                    )
                )
    return ValidationReport(
        issues=issues,
        passed=not any(issue.severity in {"error", "critical"} for issue in issues),
    )


class ValidationAgent(ResearchAgent[ValidationReport]):
    """Validate itinerary constraints without delegating pass/fail to an LLM."""

    name = "validation"
    description = (
        "Deterministic budget, schedule, distance, and consistency validation."
    )
    output_model = ValidationReport
    prompt_name = "validation_v1.txt"
    role = "simple"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a deterministic validation report."""
        requirements = self.requirements(context)
        itinerary_result = context.upstream_results.get("itinerary")
        budget_result = context.upstream_results.get("budget")
        weather_result = context.upstream_results.get("weather")
        plan = (
            ItineraryPlan.model_validate(itinerary_result.data)
            if itinerary_result and itinerary_result.data
            else ItineraryPlan()
        )
        budget = (
            BudgetReport.model_validate(budget_result.data)
            if budget_result and budget_result.data
            else None
        )
        weather = (
            WeatherOutput.model_validate(weather_result.data)
            if weather_result and weather_result.data
            else None
        )
        report = validate_itinerary(plan, requirements, budget, weather)
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data=report.model_dump(mode="json"),
            sources=["deterministic-validation"],
            confidence=1.0,
        )


def _validate_day(day: ItineraryDayPlan, issues: list[ValidationIssue]) -> None:
    """Check time overlaps, opening hours, and excessive daily travel."""
    previous_end: time | None = None
    previous_coordinates: tuple[float, float] | None = None
    travel_km = Decimal("0")
    for item in day.items:
        if (
            previous_end is not None
            and item.start_time is not None
            and item.start_time < previous_end
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    type="schedule",
                    message=f"Items overlap on day {day.day_number}.",
                    suggested_fix="Move the later item after the preceding stop.",
                    auto_fixable=True,
                )
            )
        if item.start_time and item.end_time and item.end_time <= item.start_time:
            issues.append(
                ValidationIssue(
                    severity="error",
                    type="timing",
                    message=f"{item.title} has an invalid time window.",
                    suggested_fix="Recalculate the item duration.",
                    auto_fixable=True,
                )
            )
        if (
            item.opening_hours
            and item.start_time
            and not _within_opening_hours(item.start_time, item.opening_hours)
        ):
            issues.append(
                ValidationIssue(
                    severity="error",
                    type="timing",
                    message=f"{item.title} starts outside its opening hours.",
                    suggested_fix="Move the stop into its opening window.",
                    auto_fixable=True,
                )
            )
        if item.latitude is not None and item.longitude is not None:
            coordinates = (item.latitude, item.longitude)
            if previous_coordinates:
                segment_km = Decimal(
                    str(distance_km(previous_coordinates, coordinates))
                )
                travel_km += segment_km
                if (
                    previous_end is not None
                    and item.start_time is not None
                    and segment_km > Decimal("50")
                ):
                    available_minutes = (
                        item.start_time.hour * 60
                        + item.start_time.minute
                        - previous_end.hour * 60
                        - previous_end.minute
                    )
                    required_minutes = segment_km / Decimal("80") * Decimal("60")
                    if available_minutes < required_minutes:
                        issues.append(
                            ValidationIssue(
                                severity="error",
                                type="timing",
                                message=(
                                    f"Impossible transition to {item.title} on "
                                    f"day {day.day_number}."
                                ),
                                suggested_fix=(
                                    "Allow enough travel time or move the stop closer."
                                ),
                                auto_fixable=True,
                            )
                        )
            previous_coordinates = coordinates
        previous_end = item.end_time or previous_end
    if travel_km > Decimal("250"):
        issues.append(
            ValidationIssue(
                severity="warning",
                type="distance",
                message=f"Day {day.day_number} includes excessive travel.",
                suggested_fix="Cluster stops geographically.",
                auto_fixable=True,
            )
        )


def _within_opening_hours(start: time, windows: list[str]) -> bool:
    """Parse simple HH:MM-HH:MM opening-hour windows."""
    for window in windows:
        try:
            opening, closing = window.split("-", maxsplit=1)
            lower = time.fromisoformat(opening)
            upper = time.fromisoformat(closing)
        except ValueError:
            continue
        if lower <= start <= upper:
            return True
    return False
