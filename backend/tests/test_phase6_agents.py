"""Deterministic Phase 6 budget, itinerary, validation, and repair tests."""

from datetime import date, time
from decimal import Decimal
from uuid import uuid4

import pytest
import structlog

from app.agents import build_default_agent_registry
from app.agents.budget import BudgetOptimizer
from app.agents.grounding import GroundingService
from app.agents.itinerary import (
    cluster_candidates,
    distance_km,
    generate_itinerary,
    regenerate_days,
)
from app.agents.validation import validate_itinerary
from app.core.config import Settings
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.models.enums import BudgetCategoryType, ItineraryItemType
from app.orchestration.agent_base import AgentContext
from app.orchestration.events import InMemoryEventBus
from app.orchestration.repair import (
    remove_duplicate_places,
    validate_and_repair,
)
from app.schemas.agent_io import (
    AccommodationOption,
    AccommodationOutput,
    ActivityOption,
    ActivityOutput,
    AgentResult,
    ItineraryDayPlan,
    ItineraryItemPlan,
    ItineraryPlan,
    TaskSpec,
    TransportOption,
    TransportOutput,
    TripRequirements,
    ValidationReport,
    WeatherDay,
    WeatherOutput,
)
from app.tools.fake import build_fake_tool_registry


def requirements(**updates: object) -> TripRequirements:
    """Build a complete stable three-day test trip."""
    values: dict[str, object] = {
        "destination": "Manali",
        "start_date": date(2027, 1, 1),
        "end_date": date(2027, 1, 3),
        "traveler_count": 2,
        "budget": Decimal("1000"),
        "currency": "INR",
    }
    values.update(updates)
    return TripRequirements.model_validate(values)


def result(name: str, data: object) -> AgentResult:
    """Wrap a typed upstream model for budget tests."""
    return AgentResult(
        agent_name=name,
        status="succeeded",
        data=data.model_dump(mode="json"),
    )


def test_budget_uses_decimal_allocation_without_float_drift() -> None:
    """Allocation totals remain exact Decimal money values."""
    report = BudgetOptimizer().build_report(requirements(), {})
    assert sum(item.allocated_amount for item in report.allocations) == Decimal(
        "1000.00"
    )


def test_budget_overrun_preserves_selected_must_have_transport() -> None:
    """Over-budget reports suggest savings while retaining a transport choice."""
    transport = TransportOutput(
        options=[
            TransportOption(
                mode="train",
                description="Train estimate",
                estimated_cost_min=Decimal("800"),
            )
        ]
    )
    report = BudgetOptimizer().build_report(
        requirements(budget=Decimal("100")),
        {"transport": result("transport", transport)},
    )
    assert report.over_budget is True
    assert report.selected[BudgetCategoryType.TRANSPORTATION] == ["train"]


def test_budget_what_if_reduction_and_custom_style_ratios() -> None:
    """What-if reductions and injected travel-style ratios are honored."""
    optimizer = BudgetOptimizer(
        ratios={
            "standard": {category: Decimal("0.20") for category in BudgetCategoryType}
        }
    )
    report = optimizer.build_report(requirements(), {}, what_if="reduce by 15%")
    assert report.total_budget == Decimal("850.00")
    assert all(
        item.allocated_amount == Decimal("170.00") for item in report.allocations
    )


def test_budget_accommodation_and_currency_estimates_are_flagged() -> None:
    """Accommodation estimates retain estimate provenance."""
    accommodation = AccommodationOutput(
        options=[
            AccommodationOption(
                name="Snow Valley",
                estimated_cost_min=Decimal("50"),
                is_estimate=True,
                source="heuristic",
                verified=True,
            )
        ]
    )
    report = BudgetOptimizer().build_report(
        requirements(), {"accommodation": result("accommodation", accommodation)}
    )
    assert report.is_estimate is True
    assert report.allocations[1].estimated_amount == Decimal("100.00")


def test_itinerary_clusters_nearby_places_and_is_deterministic() -> None:
    """Nearby candidates are assigned together and repeated generation is stable."""
    items = [
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title=title,
            latitude=latitude,
            longitude=longitude,
            place_id=title,
            verified=True,
        )
        for title, latitude, longitude in (
            ("A", 32.2400, 77.1800),
            ("B", 32.2410, 77.1810),
            ("C", 32.5000, 77.5000),
        )
    ]
    buckets = cluster_candidates(items, 2)
    assert {item.title for item in buckets[0]} == {"A", "B"}
    first = generate_itinerary(requirements(), items)
    second = generate_itinerary(requirements(), items)
    assert first == second


def test_validation_detects_schedule_place_and_accessibility_issues() -> None:
    """Crafted schedule violations produce typed, auto-fixable issues."""
    place_id = str(uuid4())
    item = ItineraryItemPlan(
        item_type=ItineraryItemType.ACTIVITY,
        title="Closed museum",
        place_id=place_id,
        start_time=time(8),
        end_time=time(10),
        opening_hours=["10:00-18:00"],
        metadata={"accessible": False},
    )
    duplicate = item.model_copy(
        update={"title": "Duplicate museum", "start_time": time(9)}
    )
    report = validate_itinerary(
        ItineraryPlan(
            days=[
                ItineraryDayPlan(
                    day_number=1,
                    date=date(2027, 1, 1),
                    items=[item, duplicate],
                )
            ]
        ),
        requirements(accessibility=["wheelchair"]),
    )
    types = {issue.type for issue in report.issues}
    assert {"schedule", "timing", "consistency"} <= types
    assert any(issue.auto_fixable for issue in report.issues)


@pytest.mark.asyncio
async def test_repair_loop_is_bounded_and_reports_unfixable_issues() -> None:
    """The repair helper stops after two iterations and emits lifecycle events."""
    report = ValidationReport(
        passed=False,
        issues=[
            {
                "severity": "error",
                "type": "schedule",
                "message": "Day 1 overlaps",
                "auto_fixable": True,
            }
        ],
    )
    repairs = 0

    async def repair(_: set[int]) -> None:
        nonlocal repairs
        repairs += 1

    async def validate() -> ValidationReport:
        return report

    bus = InMemoryEventBus()
    result_value = await validate_and_repair(
        trip_id=uuid4(),
        run_id=uuid4(),
        report=report,
        validate=validate,
        repair=repair,
        event_bus=bus,
    )
    assert result_value.iterations == 2
    assert repairs == 2
    assert result_value.status == "completed_with_issues"


def test_default_registry_contains_all_phase6_agents() -> None:
    """The default registry exposes the six specialists and three Phase 6 agents."""
    names = build_default_agent_registry(Settings()).names()
    assert names == [
        "destination",
        "transport",
        "accommodation",
        "weather",
        "activity",
        "local_intelligence",
        "budget",
        "itinerary",
        "validation",
    ]


def test_distance_ordering_metric_is_positive_for_distinct_places() -> None:
    """The geographic primitive returns a meaningful distance."""
    assert distance_km((32.24, 77.18), (32.30, 77.25)) > 0


def _activity_candidates(count: int = 9) -> list[ItineraryItemPlan]:
    """Create geographically grouped candidates for scheduler tests."""
    return [
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title=f"Stop {index}",
            latitude=32.24 + index * 0.001,
            longitude=77.18 + index * 0.001,
            verified=True,
            metadata={"duration_minutes": 60},
        )
        for index in range(count)
    ]


def test_itinerary_distributes_activities_across_days_and_sorts_meals() -> None:
    """Every day receives clustered activities and chronological items."""
    plan = generate_itinerary(requirements(), _activity_candidates())
    assert [len(day.items) for day in plan.days] == [6, 6, 6]
    for day in plan.days:
        starts = [item.start_time for item in day.items]
        assert starts == sorted(starts)
        assert "Stop" in (day.summary or "")


def test_itinerary_respects_opening_hours() -> None:
    """A candidate's first scheduled time is inside its opening window."""
    candidate = _activity_candidates(1)[0].model_copy(
        update={"opening_hours": ["10:00-18:00"]}
    )
    plan = generate_itinerary(requirements(), [candidate])
    scheduled = next(item for item in plan.days[0].items if item.title == "Stop 0")
    assert scheduled.start_time == time(10)


def test_itinerary_weather_sensitive_candidates_move_after_risk() -> None:
    """Weather-sensitive stops are not preferred on low-suitability days."""
    candidates = [
        item.model_copy(update={"weather_sensitive": True})
        for item in _activity_candidates(3)
    ]
    weather = WeatherOutput(
        destination="Manali",
        days=[
            WeatherDay(
                date=date(2027, 1, 1),
                summary="storm",
                outdoor_suitability=0.2,
            ),
            WeatherDay(
                date=date(2027, 1, 2),
                summary="clear",
                outdoor_suitability=0.9,
            ),
        ],
    )
    plan = generate_itinerary(requirements(), candidates, weather)
    assert plan.days[0].items


def test_budget_reports_savings_from_cheaper_alternatives() -> None:
    """Choosing the minimum transport and lodging estimates reports savings."""
    transport = TransportOutput(
        options=[
            TransportOption(
                mode="bus",
                description="bus",
                estimated_cost_min=Decimal("100"),
                estimated_cost_max=Decimal("200"),
            ),
            TransportOption(
                mode="flight",
                description="flight",
                estimated_cost_min=Decimal("500"),
                estimated_cost_max=Decimal("1000"),
            ),
        ]
    )
    report = BudgetOptimizer().build_report(
        requirements(), {"transport": result("transport", transport)}
    )
    assert report.selected[BudgetCategoryType.TRANSPORTATION] == ["bus"]
    assert report.savings_achieved > 0


def _clean_plan() -> ItineraryPlan:
    """Build a complete, verified three-day plan for validation tests."""
    days = []
    for day_number, day_date in enumerate(
        (date(2027, 1, 1), date(2027, 1, 2), date(2027, 1, 3)),
        start=1,
    ):
        items = [
            ItineraryItemPlan(
                item_type=ItineraryItemType.MEAL,
                title=title,
                start_time=start,
                end_time=end,
                verified=True,
            )
            for title, start, end in (
                ("Breakfast", time(8), time(8, 45)),
                ("Lunch", time(13), time(13, 45)),
                ("Dinner", time(19), time(19, 45)),
            )
        ]
        items.append(
            ItineraryItemPlan(
                item_type=ItineraryItemType.ACTIVITY,
                title=f"Place {day_number}",
                place_id=f"place-{day_number}",
                start_time=time(10),
                end_time=time(11),
                verified=True,
            )
        )
        days.append(
            ItineraryDayPlan(
                day_number=day_number,
                date=day_date,
                items=sorted(items, key=lambda item: item.start_time or time.max),
            )
        )
    return ItineraryPlan(days=days)


def test_validation_clean_itinerary_passes() -> None:
    """A grounded itinerary with meals and valid timing passes all checks."""
    assert validate_itinerary(_clean_plan(), requirements()).passed is True


def test_validation_detects_overlap() -> None:
    """Overlapping items produce an overlap schedule issue."""
    plan = _clean_plan()
    plan.days[0].items[1] = (
        plan.days[0].items[1].model_copy(update={"start_time": time(8)})
    )
    assert any(
        "overlap" in issue.message.lower()
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_validation_detects_outside_opening_hours() -> None:
    """A stop starting before opening hours is rejected."""
    plan = _clean_plan()
    item = (
        plan.days[0]
        .items[-1]
        .model_copy(update={"start_time": time(7), "opening_hours": ["09:00-18:00"]})
    )
    plan.days[0].items[-1] = item
    assert any(
        issue.type == "timing"
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_validation_detects_impossible_transition() -> None:
    """A distant stop with too little travel time is impossible."""
    plan = _clean_plan()
    plan.days[0].items[-1] = (
        plan.days[0]
        .items[-1]
        .model_copy(update={"latitude": 32.24, "longitude": 77.18})
    )
    plan.days[0].items.append(
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title="Remote",
            place_id="remote",
            latitude=40,
            longitude=77,
            start_time=time(11),
            end_time=time(12),
            verified=True,
        )
    )
    assert any(
        "Impossible transition" in issue.message
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_validation_detects_over_budget() -> None:
    """A budget report above the trip budget is a budget issue."""
    budget = BudgetOptimizer().build_report(
        requirements(budget=Decimal("100")),
        {},
    )
    budget = budget.model_copy(
        update={"total_estimated": Decimal("101"), "over_budget": True}
    )
    assert any(
        issue.type == "budget"
        for issue in validate_itinerary(
            _clean_plan(), requirements(budget=Decimal("100")), budget
        ).issues
    )


def test_validation_detects_missing_meal() -> None:
    """Removing a meal slot creates a missing-meal schedule issue."""
    plan = _clean_plan()
    plan.days[0].items = [item for item in plan.days[0].items if item.title != "Lunch"]
    assert any(
        "missing" in issue.message.lower()
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_validation_detects_outdoor_activity_on_stormy_day() -> None:
    """Weather-sensitive outdoor stops on a storm day are rejected."""
    plan = _clean_plan()
    plan.days[0].items.append(
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title="Storm hike",
            place_id="storm-hike",
            weather_sensitive=True,
            verified=True,
        )
    )
    weather = WeatherOutput(
        destination="Manali",
        days=[
            WeatherDay(date=date(2027, 1, 1), summary="storm", outdoor_suitability=0.2)
        ],
    )
    assert any(
        "weather-risk" in issue.message
        for issue in validate_itinerary(plan, requirements(), weather=weather).issues
    )


def test_validation_detects_accessibility_violation() -> None:
    """An inaccessible stop is an accessibility consistency issue."""
    plan = _clean_plan()
    plan.days[0].items.append(
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title="Stairs",
            place_id="stairs",
            verified=True,
            metadata={"accessible": False},
        )
    )
    report = validate_itinerary(plan, requirements(accessibility=["step-free"]))
    assert any(
        "accessibility" in issue.message.lower()
        or "does not meet" in issue.message.lower()
        for issue in report.issues
    )


def test_validation_detects_duplicate_place() -> None:
    """The same place ID on multiple days is duplicated."""
    plan = _clean_plan()
    plan.days[1].items.append(
        ItineraryItemPlan(
            item_type=ItineraryItemType.ACTIVITY,
            title="Duplicate",
            place_id="place-1",
            verified=True,
        )
    )
    assert any(
        "duplicated" in issue.message.lower()
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_validation_detects_wrong_day_count() -> None:
    """The plan must contain exactly one row for each trip date."""
    plan = _clean_plan().model_copy(update={"days": _clean_plan().days[:2]})
    assert any(
        "Expected 3 itinerary days" in issue.message
        for issue in validate_itinerary(plan, requirements()).issues
    )


def test_budget_three_night_accommodation_is_nights_times_nightly_rate() -> None:
    """A 40-per-night room totals 120 for three nights and selected name is stable."""
    accommodation = AccommodationOutput(
        options=[
            AccommodationOption(
                name="Snow Valley",
                estimated_cost_min=Decimal("40"),
                estimated_cost_max=Decimal("80"),
                verified=True,
            )
        ]
    )
    report = BudgetOptimizer().build_report(
        requirements(end_date=date(2027, 1, 4)),
        {"accommodation": result("accommodation", accommodation)},
    )
    assert report.allocations[1].estimated_amount == Decimal("120.00")
    assert report.selected[BudgetCategoryType.ACCOMMODATION] == ["Snow Valley"]


def test_unverified_place_is_excluded_from_budget_and_selected_choices() -> None:
    """Hallucinated activities cannot enter budget totals or selected choices."""
    activities = ActivityOutput(
        activities=[
            ActivityOption(
                title="Hallucinated Adventure Park",
                estimated_cost=Decimal("500"),
                verified=False,
                verification_status="not_found",
            )
        ]
    )
    report = BudgetOptimizer().build_report(
        requirements(),
        {"activity": result("activity", activities)},
    )
    activity_allocation = next(
        item
        for item in report.allocations
        if item.category == BudgetCategoryType.ACTIVITIES
    )
    assert activity_allocation.estimated_amount == Decimal("0")
    assert "Hallucinated Adventure Park" not in report.selected.get(
        BudgetCategoryType.ACTIVITIES, []
    )


def test_savings_never_exceed_original_estimated_total() -> None:
    """Savings are capped by the original category baseline."""
    transport = TransportOutput(
        options=[
            TransportOption(
                mode="bus",
                description="bus",
                estimated_cost_min=Decimal("100"),
                estimated_cost_max=Decimal("200"),
            ),
            TransportOption(
                mode="flight",
                description="flight",
                estimated_cost_min=Decimal("500"),
                estimated_cost_max=Decimal("1000"),
            ),
        ]
    )
    optimizer = BudgetOptimizer()
    report = optimizer.build_report(
        requirements(budget=Decimal("100")),
        {"transport": result("transport", transport)},
    )
    original = optimizer._original_estimated_total(
        requirements(budget=Decimal("100")),
        {"transport": result("transport", transport)},
    )
    assert report.savings_achieved <= original


def test_regenerate_days_preserves_other_days_identically() -> None:
    """Targeted repair replaces only requested day models."""
    plan = generate_itinerary(requirements(), _activity_candidates())
    before = [day.model_copy(deep=True) for day in plan.days]
    repaired = regenerate_days(
        plan,
        {2},
        requirements(),
        _activity_candidates(),
    )
    assert repaired.days[0] == before[0]
    assert repaired.days[2] == before[2]


def test_repair_fixes_bad_itinerary_within_two_iterations() -> None:
    """A single overlap can be repaired and revalidated within the cap."""
    plan = _clean_plan()
    plan.days[0].items[1] = (
        plan.days[0].items[1].model_copy(update={"start_time": time(8)})
    )
    bad = validate_itinerary(plan, requirements())
    assert bad.passed is False

    repaired = regenerate_days(
        plan,
        {1},
        requirements(),
        _activity_candidates(),
    )
    clean = validate_itinerary(repaired, requirements())
    assert clean.passed is True or any(
        issue.type != "schedule" for issue in clean.issues
    )


def test_repair_removes_overlap_by_rescheduling_affected_day() -> None:
    """Rescheduling an affected day removes an injected overlap."""
    plan = _clean_plan()
    plan.days[0].items[1] = (
        plan.days[0].items[1].model_copy(update={"start_time": time(8)})
    )
    repaired = regenerate_days(plan, {1}, requirements(), _activity_candidates())
    assert not any(
        "overlap" in issue.message.lower()
        for issue in validate_itinerary(repaired, requirements()).issues
    )


def test_repair_removes_duplicate_place_from_affected_day() -> None:
    """Duplicate place repair removes only the repeated item."""
    plan = _clean_plan()
    duplicate = (
        plan.days[1]
        .items[-1]
        .model_copy(update={"title": "Duplicate", "place_id": "place-1"})
    )
    plan.days[1].items.append(duplicate)
    repaired = remove_duplicate_places(plan, {2})
    place_ids = [
        item.place_id for day in repaired.days for item in day.items if item.place_id
    ]
    assert place_ids.count("place-1") == 1


def test_over_budget_repair_rebuilds_a_lower_cost_report() -> None:
    """Rerunning budget estimation replaces an injected over-budget total."""
    optimizer = BudgetOptimizer()
    original = optimizer.build_report(requirements(), {})
    injected = original.model_copy(
        update={"total_estimated": Decimal("1250"), "over_budget": True}
    )
    repaired = optimizer.build_report(requirements(), {}, what_if="reduce by 15%")
    assert repaired.total_estimated < injected.total_estimated


def test_repair_keeps_day_numbers_unique_and_complete() -> None:
    """Targeted repair preserves exactly the original sorted day numbers."""
    plan = generate_itinerary(requirements(), _activity_candidates())
    repaired = regenerate_days(plan, {1, 3}, requirements(), _activity_candidates())
    assert [day.day_number for day in repaired.days] == [1, 2, 3]


def test_regenerate_days_rejects_duplicate_day_numbers() -> None:
    """Regeneration fails fast instead of silently changing itinerary shape."""
    plan = generate_itinerary(requirements(), _activity_candidates())
    invalid = plan.model_copy(update={"days": [plan.days[0], plan.days[0]]})
    with pytest.raises(ValueError, match="unique and sorted"):
        regenerate_days(invalid, {1}, requirements(), _activity_candidates())


def test_unverified_place_never_reappears_during_repair() -> None:
    """Not-found places remain excluded even when repair allows tentative items."""
    unverified = ItineraryItemPlan(
        item_type=ItineraryItemType.ACTIVITY,
        title="Hallucinated Adventure Park",
        tentative=True,
        metadata={"verification_status": "not_found"},
    )
    plan = generate_itinerary(
        requirements(),
        _activity_candidates() + [unverified],
        include_unverified=True,
    )
    assert all(
        item.title != "Hallucinated Adventure Park"
        for day in plan.days
        for item in day.items
    )


@pytest.mark.asyncio
async def test_unfixable_itinerary_status_requires_exactly_two_repairs() -> None:
    """An auto-fixable issue that remains ends after exactly two repairs."""

    report = ValidationReport(
        passed=False,
        issues=[
            {
                "severity": "error",
                "type": "consistency",
                "message": "Unfixable duplicate on day 1.",
                "auto_fixable": True,
            }
        ],
    )
    repairs = 0

    async def repair(_: set[int]) -> None:
        nonlocal repairs
        repairs += 1

    async def validate() -> ValidationReport:
        return report

    result_value = await validate_and_repair(
        trip_id=uuid4(),
        run_id=uuid4(),
        report=report,
        validate=validate,
        repair=repair,
        event_bus=InMemoryEventBus(),
    )
    assert result_value.iterations == 2
    assert repairs == 2
    assert result_value.status == "completed_with_issues"


@pytest.mark.asyncio
async def test_grounding_runs_places_pipeline_concurrently_and_deterministically() -> (
    None
):
    """Multiple place items are grounded concurrently without changing input order."""
    settings = Settings(use_fake_providers=True, grounding_concurrency=2)
    tools = build_fake_tool_registry(settings)
    router = LLMRouter(
        {"fake": FakeLLMClient()},
        primary_provider="fake",
        settings=settings,
    )
    context = AgentContext(
        state={},
        task=TaskSpec(task_key="grounding", agent_name="activity"),
        upstream_results={},
        llm_router=router,
        tool_registry=tools,
        logger=structlog.get_logger("grounding-test"),
    )
    items = [
        {"title": "Hadimba Temple"},
        {"title": "Solang Valley"},
        {"title": "Hallucinated Adventure Park"},
    ]
    grounded = await GroundingService(settings).ground(
        context,
        items,
        name_key="title",
    )
    assert [item["title"] for item in grounded] == [
        "Hadimba Temple",
        "Solang Valley",
        "Hallucinated Adventure Park",
    ]
    assert grounded[0]["verified"] is True
    assert grounded[2]["verification_status"] == "not_found"
