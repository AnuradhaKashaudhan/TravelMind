"""Bounded validation and targeted itinerary repair orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.orchestration.events import EventBus, EventType, WorkflowEvent
from app.schemas.agent_io import ItineraryPlan, ValidationIssue, ValidationReport


@dataclass(frozen=True)
class RepairLoopResult:
    """Final validation result and number of targeted repair iterations."""

    report: ValidationReport
    iterations: int
    status: str


def remove_duplicate_places(
    plan: ItineraryPlan,
    day_numbers: set[int],
) -> ItineraryPlan:
    """Remove repeated place IDs only from affected days."""
    seen: set[str] = set()
    updated_days = []
    for day in plan.days:
        kept = []
        for item in day.items:
            if day.day_number in day_numbers and item.place_id in seen:
                continue
            if item.place_id:
                seen.add(item.place_id)
            kept.append(item)
        updated_days.append(
            day.model_copy(update={"items": kept})
            if day.day_number in day_numbers
            else day
        )
    return plan.model_copy(update={"days": updated_days})


async def validate_and_repair(
    *,
    trip_id: UUID,
    run_id: UUID,
    report: ValidationReport,
    validate: Callable[[], Awaitable[ValidationReport]],
    repair: Callable[[set[int]], Awaitable[None]],
    repair_with_issues: (
        Callable[[set[int], Sequence[ValidationIssue]], Awaitable[None]] | None
    ) = None,
    event_bus: EventBus,
    max_iterations: int = 2,
) -> RepairLoopResult:
    """Repair only auto-fixable affected work, with a hard iteration cap."""
    current = report
    iterations = 0
    while True:
        await event_bus.publish(
            WorkflowEvent(
                event_type=EventType.VALIDATION_STARTED,
                trip_id=trip_id,
                run_id=run_id,
                payload={"iteration": iterations},
            )
        )
        blocking = [
            issue
            for issue in current.issues
            if issue.severity in {"error", "critical"} or issue.auto_fixable
        ]
        if not blocking:
            await event_bus.publish(
                WorkflowEvent(
                    event_type=EventType.VALIDATION_PASSED,
                    trip_id=trip_id,
                    run_id=run_id,
                    payload={"iteration": iterations},
                )
            )
            return RepairLoopResult(current, iterations, "passed")
        if iterations >= max_iterations or not all(
            issue.auto_fixable for issue in blocking
        ):
            await event_bus.publish(
                WorkflowEvent(
                    event_type=EventType.VALIDATION_FAILED,
                    trip_id=trip_id,
                    run_id=run_id,
                    payload={"iteration": iterations},
                )
            )
            return RepairLoopResult(current, iterations, "completed_with_issues")
        affected_days = _affected_days(blocking)
        await event_bus.publish(
            WorkflowEvent(
                event_type=EventType.REPAIR_STARTED,
                trip_id=trip_id,
                run_id=run_id,
                payload={"iteration": iterations, "day_count": len(affected_days)},
            )
        )
        if repair_with_issues is not None:
            await repair_with_issues(affected_days, blocking)
        else:
            await repair(affected_days)
        iterations += 1
        await event_bus.publish(
            WorkflowEvent(
                event_type=EventType.REPAIR_COMPLETED,
                trip_id=trip_id,
                run_id=run_id,
                payload={"iteration": iterations},
            )
        )
        current = await validate()


def _affected_days(issues: Sequence[object]) -> set[int]:
    """Read day numbers from issue messages when repair metadata is unavailable."""
    days: set[int] = set()
    for issue in issues:
        message = getattr(issue, "message", "")
        for token in str(message).replace(".", " ").split():
            if token.isdigit():
                days.add(int(token))
    return days
