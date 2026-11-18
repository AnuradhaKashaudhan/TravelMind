"""Run the complete nine-agent Phase 6 plan without database or network by default."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
from app.agents import build_default_agent_registry
from app.agents.budget import BudgetOptimizer
from app.agents.itinerary import regenerate_days
from app.agents.validation import validate_itinerary
from app.core.config import Settings
from app.evaluation.metrics import compute_metrics
from app.llm.factory import build_llm_router
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import ItineraryItemType
from app.orchestration.agent_base import AgentContext
from app.orchestration.events import InMemoryEventBus
from app.orchestration.repair import (
    remove_duplicate_places,
    validate_and_repair,
)
from app.schemas.agent_io import (
    ActivityOutput,
    ItineraryItemPlan,
    TaskSpec,
    TripRequirements,
    ValidationIssue,
    ValidationReport,
    WeatherOutput,
)
from app.tools.fake import build_fake_tool_registry
from app.tools.registry import build_default_tool_registry

if Path("/app").is_dir():
    assert app.__file__ is not None and app.__file__.startswith("/app/app")


async def run(destination: str, fake: bool, inject_faults: bool = False) -> None:
    """Execute the nine agents and print budget, itinerary, validation, and metrics."""
    settings = Settings(use_fake_providers=fake)
    if fake:
        router: LLMRouter = LLMRouter(
            {"fake": FakeLLMClient(_fake_responses(destination))},
            primary_provider="fake",
            settings=settings,
        )
        tools = build_fake_tool_registry(settings)
    else:
        router = build_llm_router(settings)
        tools = build_default_tool_registry(settings)
    requirements = TripRequirements(
        destination=destination,
        origin="Delhi, India",
        start_date=date.today() + timedelta(days=7),
        end_date=date.today() + timedelta(days=9),
        traveler_count=2,
        budget=Decimal("1000"),
        currency="INR",
        interests=["hiking", "culture"],
        food=["local vegetarian food"],
        accessibility=["step-free where possible"],
    )
    registry = build_default_agent_registry(settings, router, tools)
    results = {}
    task_states = {}
    try:
        for name in registry.names():
            started = time.perf_counter()
            result = await registry.get(name).run(
                AgentContext(
                    state={"requirements": requirements},
                    task=TaskSpec(task_key=name, agent_name=name),
                    upstream_results={
                        dependency: results[dependency]
                        for dependency in _dependencies(name)
                        if dependency in results
                    },
                    llm_router=router,
                    tool_registry=tools,
                    logger=structlog.get_logger("full-plan-demo"),
                )
            )
            elapsed = round((time.perf_counter() - started) * 1000)
            results[name] = result
            task_states[name] = {
                "duration_ms": result.duration_ms or elapsed,
                "token_usage": result.token_usage or {},
            }
        budget = _model(results.get("budget"), "BudgetReport")
        itinerary = _model(results.get("itinerary"), "ItineraryPlan")
        validation = _model(results.get("validation"), "ValidationReport")
        repair_iterations = 0
        final_status = (
            "planned" if validation and validation.passed else "completed_with_issues"
        )
        if inject_faults and budget and itinerary and validation:
            print("\nFault injection")
            print("  planted: overlap on day 1")
            print("  planted: over-budget estimated choice")
            first_day = itinerary.days[0]
            items = list(first_day.items)
            if len(items) >= 2:
                items[1] = items[1].model_copy(
                    update={"start_time": items[0].start_time or items[1].start_time}
                )
            itinerary = itinerary.model_copy(
                update={
                    "days": [
                        first_day.model_copy(update={"items": items})
                        if day.day_number == 1
                        else day
                        for day in itinerary.days
                    ]
                }
            )
            budget = budget.model_copy(
                update={
                    "total_estimated": budget.total_budget + Decimal("250"),
                    "over_budget": True,
                }
            )
            validation = validate_itinerary(itinerary, requirements, budget)
            activity = _model(results.get("activity"), "ActivityOutput")
            activities = []
            if activity:
                activity_output = ActivityOutput.model_validate(activity.model_dump())
                for candidate in activity_output.activities:
                    activities.append(
                        ItineraryItemPlan(
                            item_type=ItineraryItemType.ACTIVITY,
                            title=candidate.title,
                            place_id=candidate.place_id,
                            latitude=candidate.latitude,
                            longitude=candidate.longitude,
                            estimated_cost=candidate.estimated_cost,
                            currency=candidate.currency,
                            verified=candidate.verified,
                            tentative=not candidate.verified,
                            is_estimate=candidate.is_estimate,
                            weather_sensitive=candidate.weather_sensitive,
                            opening_hours=candidate.opening_hours,
                            metadata={
                                "duration_minutes": candidate.duration_minutes or 90,
                                "verification_status": candidate.verification_status,
                            },
                        )
                    )
            weather_model = _model(results.get("weather"), "WeatherOutput")
            weather = (
                WeatherOutput.model_validate(weather_model.model_dump())
                if weather_model is not None
                else None
            )
            repaired = itinerary
            repaired_budget = budget
            iteration_number = 0

            async def repair(days: set[int]) -> None:
                nonlocal repaired
                repaired = regenerate_days(
                    repaired,
                    days,
                    requirements,
                    activities,
                    weather,
                    include_unverified=True,
                )

            async def repair_with_issues(
                days: set[int], issues: Sequence[ValidationIssue]
            ) -> None:
                nonlocal repaired, repaired_budget, iteration_number
                iteration_number += 1
                print(f"  iteration={iteration_number} issues_before={len(issues)}")
                target_days = days or {day.day_number for day in repaired.days}
                issue_types = {getattr(issue, "type", None) for issue in issues}
                actions: list[str] = []
                if any(
                    getattr(issue, "type", None) == "consistency"
                    and "duplicated" in getattr(issue, "message", "").lower()
                    for issue in issues
                ):
                    repaired = remove_duplicate_places(repaired, target_days)
                    actions.append("remove duplicate places")
                if "schedule" in issue_types or "timing" in issue_types:
                    repaired = regenerate_days(
                        repaired,
                        target_days,
                        requirements,
                        activities,
                        weather,
                        include_unverified=True,
                    )
                    actions.append("reschedule affected days")
                if "budget" in issue_types:
                    repaired_budget = BudgetOptimizer().build_report(
                        requirements,
                        results,
                        what_if="reduce by 15%",
                    )
                    actions.append("rerun budget what_if")
                    repaired = regenerate_days(
                        repaired,
                        target_days,
                        requirements,
                        activities,
                        weather,
                        include_unverified=True,
                    )
                print(f"  iteration={iteration_number} action={'; '.join(actions)}")

            async def revalidate() -> ValidationReport:
                report = validate_itinerary(
                    repaired, requirements, repaired_budget, weather
                )
                if iteration_number:
                    print(
                        f"  iteration={iteration_number} issues_after="
                        f"{len(report.issues)}"
                    )
                return report

            loop = await validate_and_repair(
                trip_id=uuid4(),
                run_id=uuid4(),
                report=validation,
                repair_with_issues=repair_with_issues,
                validate=revalidate,
                repair=repair,
                event_bus=InMemoryEventBus(),
            )
            repair_iterations = loop.iterations
            itinerary = repaired
            budget = repaired_budget
            validation = loop.report
            final_status = "planned" if loop.status == "passed" else loop.status
        print("\nBudget")
        if budget:
            for allocation in budget.allocations:
                print(
                    f"  {allocation.category}: "
                    f"allocated={allocation.allocated_amount} "
                    f"estimated={allocation.estimated_amount}"
                )
            print(f"  savings={budget.savings_achieved}")
            print("  selected:")
            for category, choices in budget.selected.items():
                print(f"    {category}: {', '.join(choices)}")
            for suggestion in budget.suggestions:
                print(f"  suggestion: {suggestion}")
        print("\nItinerary")
        if itinerary:
            for day in itinerary.days:
                print(f"  Day {day.day_number} ({day.date}): {day.summary}")
                for item in day.items:
                    print(
                        f"    {item.start_time or '--:--'}-{item.end_time or '--:--'} "
                        f"{item.title}"
                    )
        print("\nValidation")
        print(
            f"  passed={validation.passed if validation else False} "
            f"issues={len(validation.issues) if validation else 0}"
        )
        if validation:
            for issue in validation.issues:
                print(
                    f"  issue: {issue.type} auto_fixable={issue.auto_fixable} "
                    f"{issue.message}"
                )
        print(f"  repair_iterations={repair_iterations} final_status={final_status}")
        metrics = compute_metrics(
            budget=budget,
            itinerary=itinerary,
            validation=validation,
            task_states=task_states,
        )
        print("\nEvaluation metrics")
        print(metrics.model_dump_json(indent=2))
    finally:
        await tools.aclose()
        await router.aclose()


def _dependencies(name: str) -> tuple[str, ...]:
    """Return the default plan's upstream dependencies."""
    if name in {"accommodation", "activity"}:
        return ("destination",)
    if name == "budget":
        return (
            "destination",
            "transport",
            "accommodation",
            "activity",
            "local_intelligence",
        )
    if name == "itinerary":
        return (
            "destination",
            "transport",
            "accommodation",
            "weather",
            "activity",
            "local_intelligence",
            "budget",
        )
    if name == "validation":
        return ("itinerary", "budget", "weather")
    return ()


def _model(result: object, name: str) -> object | None:
    """Validate a result's data against a named Phase 6 model."""
    if not hasattr(result, "data") or result.data is None:
        return None
    from app.schemas import agent_io

    return getattr(agent_io, name).model_validate(result.data)


def _fake_responses(destination: str) -> list[str]:
    """Return realistic scripted research responses for a three-day Manali trip."""
    return [
        json.dumps(
            {
                "destination": destination,
                "overview": (
                    "A Himalayan valley with mountain scenery and local culture."
                ),
                "popular_attractions": [{"name": "Hadimba Temple"}],
                "hidden_gems": [
                    {"name": "Vashisht Hot Springs"},
                    {"name": "Manu Temple"},
                ],
            }
        ),
        json.dumps({"recommended_mode": "bus"}),
        json.dumps(
            {
                "options": [
                    {"name": "Snow Valley Resorts"},
                    {"name": "Hotel Snow Valley"},
                ]
            }
        ),
        json.dumps({"destination": destination}),
        json.dumps(
            {
                "activities": [
                    {"title": "Mall Road", "category": "meal", "estimated_cost": 8},
                    {
                        "title": "Hadimba Temple",
                        "category": "activity",
                        "estimated_cost": 0,
                    },
                    {
                        "title": "Solang Valley",
                        "category": "activity",
                        "estimated_cost": 12,
                        "weather_sensitive": True,
                    },
                    {
                        "title": "Manali Nature Park",
                        "category": "activity",
                        "estimated_cost": 0,
                    },
                    {
                        "title": "Vashisht Hot Springs",
                        "category": "activity",
                        "estimated_cost": 5,
                    },
                    {
                        "title": "Manu Temple",
                        "category": "activity",
                        "estimated_cost": 0,
                    },
                    {
                        "title": "Jogini Waterfall",
                        "category": "activity",
                        "estimated_cost": 8,
                        "weather_sensitive": True,
                    },
                    {
                        "title": "Tibetan Monastery",
                        "category": "activity",
                        "estimated_cost": 0,
                    },
                    {
                        "title": "Old Manali Walk",
                        "category": "activity",
                        "estimated_cost": 0,
                    },
                    {
                        "title": "Himalayan Cafe",
                        "category": "meal",
                        "estimated_cost": 10,
                    },
                    {
                        "title": "River Rafting Point",
                        "category": "activity",
                        "estimated_cost": 25,
                        "weather_sensitive": True,
                    },
                    {
                        "title": "Hallucinated Adventure Park",
                        "category": "activity",
                        "weather_sensitive": True,
                    },
                ]
            }
        ),
        json.dumps(
            {
                "destination": destination,
                "cultural_etiquette": ["Dress respectfully at temples."],
            }
        ),
    ]


def main() -> None:
    """Parse demo options and run the complete plan."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default="Manali, India")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--inject-faults", action="store_true")
    args = parser.parse_args()
    asyncio.run(
        run(
            args.destination,
            fake=not args.live,
            inject_faults=args.inject_faults,
        )
    )


if __name__ == "__main__":
    main()
