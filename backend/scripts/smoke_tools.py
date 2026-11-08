"""Manually exercise the Phase 3 travel tools with live or fake providers."""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel

from app.core.config import get_settings  # noqa: E402
from app.core.exceptions import TravelMindError  # noqa: E402
from app.tools.registry import ToolRegistry, build_default_tool_registry  # noqa: E402
from app.tools.schemas import GeocodeOutput, PlaceSearchOutput, RouteOutput  # noqa: E402


async def _run_smoke_tests(registry: ToolRegistry) -> int:
    """Run representative calls through every registered travel tool."""
    failures = 0

    async def invoke(name: str, payload: dict[str, object]) -> BaseModel | None:
        nonlocal failures
        try:
            result = await registry.get(name).run(payload)
        except (TravelMindError, ValueError) as error:
            failures += 1
            print(f"[FAIL] {name}: {error}")
            return None
        print(f"[OK] {name}\n{result.model_dump_json(indent=2)}")
        return result

    places = await invoke(
        "places_text_search",
        {"query": "Manali", "max_results": 5},
    )
    await invoke(
        "places_nearby_search",
        {"lat": 32.2396, "lng": 77.1887, "radius_m": 5000},
    )
    if isinstance(places, PlaceSearchOutput) and places.places:
        place_id = places.places[0].place_id
        await invoke("place_details", {"place_id": place_id})
        await invoke("verify_place", {"place_id": place_id})
    else:
        print("[SKIP] place_details, verify_place: no place ID returned")

    geocoded = await invoke("geocode", {"address": "Manali, India"})
    coordinates = (
        {"lat": geocoded.lat, "lng": geocoded.lng}
        if isinstance(geocoded, GeocodeOutput)
        else {"lat": 32.2396, "lng": 77.1887}
    )
    await invoke("reverse_geocode", coordinates)

    route = await invoke(
        "get_route",
        {"origin": "Manali, India", "destination": "Kullu, India", "mode": "driving"},
    )
    await invoke(
        "distance_matrix",
        {
            "origins": ["Manali, India"],
            "destinations": ["Kullu, India", "Naggar, India"],
            "mode": "driving",
        },
    )
    start = date.today() + timedelta(days=1)
    await invoke(
        "get_forecast",
        {
            "lat": 32.2396,
            "lng": 77.1887,
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=2)).isoformat(),
        },
    )
    distance_m = (
        route.distance_m
        if isinstance(route, RouteOutput) and route.distance_m > 0
        else 10000
    )
    await invoke(
        "estimate_transport",
        {
            "origin": "Manali",
            "destination": "Kullu",
            "mode": "taxi",
            "distance_m": distance_m,
        },
    )
    return 1 if failures else 0


def main() -> int:
    """Load configured providers and return a process status."""
    settings = get_settings()
    registry = build_default_tool_registry(settings)
    if settings.use_fake_providers:
        print("Using fake providers; results are placeholders.")
    return asyncio.run(_run_smoke_tests(registry))


if __name__ == "__main__":
    raise SystemExit(main())
