"""Deterministic candidate clustering and itinerary scheduling."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from app.agents.base_research import ResearchAgent
from app.models.enums import AgentRunStatus, ItineraryItemType
from app.orchestration.agent_base import AgentContext
from app.schemas.agent_io import (
    ActivityOutput,
    AgentResult,
    ItineraryDayPlan,
    ItineraryItemPlan,
    ItineraryPlan,
    TripRequirements,
    WeatherOutput,
)


def distance_km(left: tuple[float, float], right: tuple[float, float]) -> float:
    """Return a stable haversine distance between two coordinates."""
    lat1, lon1, lat2, lon2 = map(math.radians, (*left, *right))
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(value))


def cluster_candidates(
    candidates: list[ItineraryItemPlan], day_count: int
) -> list[list[ItineraryItemPlan]]:
    """Group candidates into nearby deterministic buckets."""
    buckets: list[list[ItineraryItemPlan]] = [[] for _ in range(max(day_count, 1))]
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.latitude if item.latitude is not None else 0,
            item.longitude if item.longitude is not None else 0,
            item.title.casefold(),
        ),
    )
    if len(ordered) <= len(buckets):
        for index, item in enumerate(ordered):
            buckets[index].append(item)
        return buckets
    seeds = [ordered[0]]
    while len(seeds) < len(buckets):
        candidate = max(
            (item for item in ordered if item not in seeds),
            key=lambda item: min(
                distance_km(
                    (seed.latitude or 0, seed.longitude or 0),
                    (item.latitude or 0, item.longitude or 0),
                )
                for seed in seeds
            ),
        )
        seeds.append(candidate)
    for index, item in enumerate(seeds):
        buckets[index].append(item)
    for item in (candidate for candidate in ordered if candidate not in seeds):
        if item.latitude is None or item.longitude is None:
            least_busy = min(range(len(buckets)), key=lambda index: len(buckets[index]))
            buckets[least_busy].append(item)
            continue
        closest = min(
            range(len(buckets)),
            key=lambda index: distance_km(
                (seeds[index].latitude or 0, seeds[index].longitude or 0),
                (item.latitude or 0, item.longitude or 0),
            ),
        )
        buckets[closest].append(item)
    while buckets and max(map(len, buckets)) - min(map(len, buckets)) > 1:
        largest = max(range(len(buckets)), key=lambda index: len(buckets[index]))
        smallest = min(range(len(buckets)), key=lambda index: len(buckets[index]))
        buckets[smallest].append(buckets[largest].pop())
    return buckets


def order_by_distance(items: list[ItineraryItemPlan]) -> list[ItineraryItemPlan]:
    """Use nearest-neighbour ordering from the first deterministic candidate."""
    remaining = list(items)
    if not remaining:
        return []
    current = remaining.pop(0)
    ordered = [current]
    while remaining:
        if current.latitude is None or current.longitude is None:
            ordered.extend(remaining)
            break
        current_point = (current.latitude, current.longitude)
        next_item = min(
            remaining,
            key=lambda item: distance_km(
                current_point,
                (
                    item.latitude or 0.0,
                    item.longitude or 0.0,
                ),
            ),
        )
        remaining.remove(next_item)
        ordered.append(next_item)
        current = next_item
    return ordered


def generate_itinerary(
    requirements: TripRequirements,
    activities: list[ItineraryItemPlan],
    weather: WeatherOutput | None = None,
    *,
    include_unverified: bool = False,
) -> ItineraryPlan:
    """Build a deterministic dated itinerary from grounded candidates."""
    if not requirements.start_date or not requirements.end_date:
        return ItineraryPlan()
    day_count = (requirements.end_date - requirements.start_date).days + 1
    candidates = _eligible_candidates(activities, include_unverified)
    buckets = cluster_candidates(candidates, day_count)
    weather_by_date = {item.date: item for item in (weather.days if weather else [])}
    days: list[ItineraryDayPlan] = []
    for index, bucket in enumerate(buckets):
        current_date = requirements.start_date + timedelta(days=index)
        day_weather = weather_by_date.get(current_date)
        if day_weather and day_weather.outdoor_suitability < 0.5:
            bucket.sort(
                key=lambda item: (item.weather_sensitive, item.title.casefold())
            )
        else:
            bucket = order_by_distance(bucket)
        scheduled = _schedule_day(bucket, current_date)
        scheduled = _insert_meals(scheduled, current_date)
        scheduled.sort(key=lambda item: item.start_time or time.max)
        stop_names = [
            item.title
            for item in scheduled
            if item.item_type
            in {ItineraryItemType.ACTIVITY, ItineraryItemType.ACCOMMODATION}
        ]
        days.append(
            ItineraryDayPlan(
                day_number=index + 1,
                date=current_date,
                summary=(
                    f"Stops: {', '.join(stop_names)}."
                    if stop_names
                    else (
                        "Candidate activities were insufficient for this day; "
                        "meals are scheduled."
                    )
                ),
                items=scheduled,
            )
        )
    return ItineraryPlan(days=days)


def regenerate_days(
    plan: ItineraryPlan,
    day_numbers: set[int],
    requirements: TripRequirements,
    activities: list[ItineraryItemPlan],
    weather: WeatherOutput | None = None,
    *,
    include_unverified: bool = False,
) -> ItineraryPlan:
    """Regenerate only selected day numbers and preserve all other models."""
    original_numbers = [day.day_number for day in plan.days]
    if original_numbers != sorted(set(original_numbers)):
        raise ValueError("Itinerary day numbers must be unique and sorted")
    if not day_numbers <= set(original_numbers):
        raise ValueError("Cannot regenerate a day that is not in the itinerary")
    replacement = generate_itinerary(
        requirements,
        activities,
        weather,
        include_unverified=include_unverified,
    )
    by_number = {day.day_number: day for day in replacement.days}
    if set(by_number) != set(original_numbers):
        raise ValueError("Regeneration changed the itinerary day count")
    repaired = ItineraryPlan(
        days=[
            by_number[day.day_number] if day.day_number in day_numbers else day
            for day in plan.days
        ]
    )
    repaired_numbers = [day.day_number for day in repaired.days]
    if repaired_numbers != original_numbers:
        raise ValueError("Regeneration changed itinerary day ordering")
    return repaired


def _eligible_candidates(
    activities: list[ItineraryItemPlan],
    include_unverified: bool,
) -> list[ItineraryItemPlan]:
    """Share the grounded/tentative candidate policy across generation and repair."""
    return [
        item
        for item in activities
        if item.verified
        or (
            include_unverified
            and item.tentative
            and item.metadata.get("verification_status") != "not_found"
        )
    ]


class ItineraryGenerationAgent(ResearchAgent[ItineraryPlan]):
    """Generate an itinerary using deterministic scheduling and typed evidence."""

    name = "itinerary"
    description = "Deterministic, weather-aware itinerary generation."
    output_model = ItineraryPlan
    prompt_name = "itinerary_generation_v1.txt"
    role = "itinerary"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a grounded itinerary with optional tentative suggestions."""
        requirements = self.requirements(context)
        activity_result = context.upstream_results.get("activity")
        activity_output = (
            ActivityOutput.model_validate(activity_result.data)
            if activity_result and activity_result.data
            else ActivityOutput()
        )
        activities = [
            ItineraryItemPlan(
                item_type=(
                    ItineraryItemType.MEAL
                    if activity.category == "meal"
                    else ItineraryItemType.ACTIVITY
                ),
                title=activity.title,
                description=activity.description,
                place_id=activity.place_id,
                latitude=activity.latitude,
                longitude=activity.longitude,
                estimated_cost=activity.estimated_cost,
                currency=activity.currency,
                source_agent="activity",
                verified=activity.verified,
                tentative=not activity.verified,
                is_estimate=activity.is_estimate,
                weather_sensitive=activity.weather_sensitive,
                opening_hours=activity.opening_hours,
                metadata={
                    "duration_minutes": activity.duration_minutes or 90,
                    "verification_status": activity.verification_status,
                },
            )
            for activity in activity_output.activities
            if activity.category != "meal"
        ]
        destination_result = context.upstream_results.get("destination")
        if destination_result and destination_result.data:
            destination_output = destination_result.data
            for place in destination_output.get(
                "popular_attractions", []
            ) + destination_output.get("hidden_gems", []):
                title = place.get("name")
                if title and not any(
                    item.title.casefold() == title.casefold() for item in activities
                ):
                    activities.append(
                        ItineraryItemPlan(
                            item_type=ItineraryItemType.ACTIVITY,
                            title=title,
                            description=place.get("description"),
                            place_id=place.get("place_id"),
                            latitude=place.get("latitude", place.get("lat")),
                            longitude=place.get("longitude", place.get("lng")),
                            source_agent="destination",
                            verified=bool(place.get("verified", False)),
                            tentative=not bool(place.get("verified", False)),
                            metadata={"duration_minutes": 90},
                        )
                    )
        weather_result = context.upstream_results.get("weather")
        weather = (
            WeatherOutput.model_validate(weather_result.data)
            if weather_result and weather_result.data
            else None
        )
        plan = generate_itinerary(
            requirements,
            activities,
            weather,
            include_unverified=bool(context.task.inputs.get("include_unverified")),
        )
        plan = _add_logistics(plan, context)
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data=plan.model_dump(mode="json"),
            sources=["deterministic-scheduler"],
            confidence=1.0 if plan.days else 0.3,
        )


def _add_logistics(plan: ItineraryPlan, context: AgentContext) -> ItineraryPlan:
    """Add the non-optional arrival, lodging, and departure legs."""
    if not plan.days:
        return plan
    transport = context.upstream_results.get("transport")
    accommodation = context.upstream_results.get("accommodation")
    first = plan.days[0]
    last = plan.days[-1]
    first_items = [
        ItineraryItemPlan(
            item_type=ItineraryItemType.TRANSPORT,
            title="Arrival transport",
            start_time=time(6, 0),
            end_time=time(7, 30),
            source_agent="transport",
            verified=False,
            tentative=True,
            is_estimate=True,
            metadata={"reason": "intercity arrival"},
        )
    ]
    last_items = [
        ItineraryItemPlan(
            item_type=ItineraryItemType.TRANSPORT,
            title="Departure transport",
            start_time=time(21, 0),
            end_time=time(23, 0),
            source_agent="transport",
            verified=False,
            tentative=True,
            is_estimate=True,
            metadata={"reason": "intercity departure"},
        )
    ]
    if transport and transport.data:
        first_items[0] = first_items[0].model_copy(
            update={
                "description": "Estimated "
                + str(transport.data.get("recommended_mode", "bus"))
            }
        )
    if accommodation and accommodation.data:
        options = accommodation.data.get("options", [])
        eligible = [
            option
            for option in options
            if option.get("verified")
            or (
                bool(context.task.inputs.get("include_unverified"))
                and option.get("verification_status") in {"not_found", "unavailable"}
            )
        ]
        if eligible:
            selected_lodging = min(
                eligible,
                key=lambda option: float(
                    option.get("estimated_cost_min")
                    or option.get("estimated_cost")
                    or math.inf
                ),
            )
            lodging = selected_lodging.get("name", "Accommodation")
            first_items.append(
                ItineraryItemPlan(
                    item_type=ItineraryItemType.ACCOMMODATION,
                    title=f"Check-in: {lodging}",
                    start_time=time(16, 0),
                    end_time=time(16, 30),
                    source_agent="accommodation",
                    verified=bool(selected_lodging.get("verified", False)),
                    tentative=not bool(selected_lodging.get("verified", False)),
                    is_estimate=True,
                )
            )
            last_items.insert(
                0,
                ItineraryItemPlan(
                    item_type=ItineraryItemType.ACCOMMODATION,
                    title=f"Check-out: {lodging}",
                    start_time=time(7, 0),
                    end_time=time(7, 30),
                    source_agent="accommodation",
                    verified=bool(selected_lodging.get("verified", False)),
                    tentative=not bool(selected_lodging.get("verified", False)),
                    is_estimate=True,
                ),
            )
    updated_days = [
        day.model_copy(
            update={
                "items": sorted(
                    (
                        (first_items + day.items)
                        if day.day_number == first.day_number
                        else (last_items + day.items)
                        if day.day_number == last.day_number
                        else day.items
                    ),
                    key=lambda item: item.start_time or time.max,
                )
            }
        )
        for day in plan.days
    ]
    return ItineraryPlan(days=updated_days)


def _schedule_day(
    items: list[ItineraryItemPlan], current_date: date
) -> list[ItineraryItemPlan]:
    """Assign bounded sequential times while respecting item durations."""
    cursor = datetime.combine(current_date, time(9, 0))
    scheduled: list[ItineraryItemPlan] = []
    for item in items[:4]:
        if item.item_type == ItineraryItemType.MEAL:
            scheduled.append(item)
            continue
        if time(12, 30) <= cursor.time() < time(14, 0):
            cursor = datetime.combine(current_date, time(14, 0))
        duration = int(item.metadata.get("duration_minutes", 90))
        if item.opening_hours:
            opening = _opening_start(item.opening_hours)
            if opening and cursor.time() < opening:
                cursor = datetime.combine(current_date, opening)
        end = cursor + timedelta(minutes=max(duration, 30))
        scheduled.append(
            item.model_copy(
                update={
                    "start_time": cursor.time(),
                    "end_time": end.time(),
                }
            )
        )
        cursor = end + timedelta(minutes=30)
    return scheduled


def _opening_start(windows: list[str]) -> time | None:
    """Return the earliest parseable opening time."""
    starts: list[time] = []
    for window in windows:
        try:
            starts.append(time.fromisoformat(window.split("-", maxsplit=1)[0]))
        except ValueError:
            continue
    return min(starts) if starts else None


def _insert_meals(
    items: list[ItineraryItemPlan], current_date: date
) -> list[ItineraryItemPlan]:
    """Insert meal slots when the candidate pool does not provide meals."""
    result = list(items)
    existing = {
        item.title.casefold()
        for item in result
        if item.item_type == ItineraryItemType.MEAL
    }
    meal_slots = (
        ("Breakfast", time(8, 0), time(8, 45)),
        ("Lunch", time(13, 0), time(13, 45)),
        ("Dinner", time(19, 0), time(19, 45)),
    )
    for title, start, end in reversed(meal_slots):
        if title.casefold() not in existing:
            result.insert(
                0,
                ItineraryItemPlan(
                    item_type=ItineraryItemType.MEAL,
                    title=title,
                    start_time=start,
                    end_time=end,
                    source_agent="itinerary",
                    verified=False,
                    tentative=True,
                    metadata={
                        "meal_slot": title.casefold(),
                        "date": current_date.isoformat(),
                    },
                ),
            )
    return sorted(result, key=lambda item: item.start_time or time.max)
