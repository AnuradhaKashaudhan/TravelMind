"""No-network tool stubs selected by ``USE_FAKE_PROVIDERS``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.config import Settings
from app.tools.base import Tool
from app.tools.registry import ToolRegistry
from app.tools.schemas import (
    DailyForecast,
    DistanceMatrixElement,
    DistanceMatrixInput,
    DistanceMatrixOutput,
    FareEstimateInput,
    FareEstimateOutput,
    ForecastInput,
    ForecastOutput,
    GeocodeInput,
    GeocodeOutput,
    Place,
    PlaceDetailsInput,
    PlaceNearbyInput,
    PlaceOutput,
    PlaceSearchInput,
    PlaceSearchOutput,
    ReverseGeocodeInput,
    RouteInput,
    RouteOutput,
    VerifyPlaceOutput,
    WeatherRisk,
)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class FakeTool(Tool[InputT, OutputT]):
    """Schema-preserving fake that returns a locally generated Pydantic result."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: type[InputT],
        output_schema: type[OutputT],
        responder: Callable[[InputT], OutputT],
    ) -> None:
        """Create a fake tool with a typed synchronous response function."""
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.output_schema = output_schema
        self.provider = "fake"
        self.responder = responder
        super().__init__(cache_ttl_s=0)

    async def _run(self, payload: InputT) -> OutputT:
        """Return the fake response for this validated request."""
        return self.responder(payload)


_FAKE_PLACES: dict[str, Place] = {
    "hadimba temple": Place(
        place_id="fake-hadimba",
        name="Hadimba Temple",
        address="Old Manali",
        lat=32.2487,
        lng=77.1809,
        rating=4.6,
        types=["tourist_attraction"],
    ),
    "solang valley": Place(
        place_id="fake-solang",
        name="Solang Valley",
        address="Solang Valley",
        lat=32.3167,
        lng=77.1566,
        rating=4.5,
        types=["tourist_attraction"],
    ),
    "snow valley resorts": Place(
        place_id="fake-snow-valley",
        name="Snow Valley Resorts",
        address="Manali",
        lat=32.2396,
        lng=77.1887,
        rating=4.2,
        price_level="3",
        types=["lodging"],
    ),
    "mall road": Place(
        place_id="fake-mall-road",
        name="Mall Road",
        address="Manali",
        lat=32.2432,
        lng=77.1892,
        rating=4.3,
        types=["restaurant", "tourist_attraction"],
    ),
}


def _fake_text_search(request: PlaceSearchInput) -> PlaceSearchOutput:
    """Return a known place only when the query names one."""
    place = _FAKE_PLACES.get(request.query.casefold().strip())
    return PlaceSearchOutput(places=[place] if place is not None else [])


def _fake_place_name(place_id: str) -> str:
    """Resolve a known fake place identifier for details responses."""
    return next(
        (place.name for place in _FAKE_PLACES.values() if place.place_id == place_id),
        "Verified Manali Place",
    )


def _fake_forecast(request: ForecastInput) -> ForecastOutput:
    """Return three deterministic days with one moderate rain risk."""
    daily = [
        DailyForecast(
            date=request.start_date + timedelta(days=index),
            temp_min_c=7 + index,
            temp_max_c=18 + index,
            precipitation_probability=35 if index == 1 else 15,
            precipitation_mm=8 if index == 1 else 1,
            wind_speed_kmh=12,
            condition_code=61 if index == 1 else 2,
        )
        for index in range(min(3, (request.end_date - request.start_date).days + 1))
    ]
    risks = [
        WeatherRisk(
            kind="heavy_rain",
            severity="moderate",
            date=request.start_date + timedelta(days=1),
            message="Pack rain protection for a possible wet day.",
        )
    ]
    return ForecastOutput(provider="fake climatology", daily=daily, risks=risks)


def build_fake_tool_registry(settings: Settings) -> ToolRegistry:
    """Build the same callable tool surface without making network requests."""
    del settings
    registry = ToolRegistry()
    tools: list[Tool[Any, Any]] = [
        FakeTool(
            name="places_text_search",
            description="Fake text search returning realistic local places.",
            input_schema=PlaceSearchInput,
            output_schema=PlaceSearchOutput,
            responder=_fake_text_search,
        ),
        FakeTool(
            name="places_nearby_search",
            description="Fake nearby search returning an empty local result set.",
            input_schema=PlaceNearbyInput,
            output_schema=PlaceSearchOutput,
            responder=lambda _: PlaceSearchOutput(places=[]),
        ),
        FakeTool(
            name="place_details",
            description="Return a realistic local fake place record.",
            input_schema=PlaceDetailsInput,
            output_schema=PlaceOutput,
            responder=lambda request: PlaceOutput(
                place_id=request.place_id,
                name=_fake_place_name(request.place_id),
                address="Manali, Himachal Pradesh, India",
                lat=32.2432,
                lng=77.1892,
                rating=4.5,
                opening_hours=["08:00-20:00"],
            ),
        ),
        FakeTool(
            name="verify_place",
            description="Return a local fake operational place result.",
            input_schema=PlaceDetailsInput,
            output_schema=VerifyPlaceOutput,
            responder=lambda request: VerifyPlaceOutput(
                place_id=request.place_id,
                exists=True,
                operational=True,
            ),
        ),
        FakeTool(
            name="geocode",
            description="Return a zero-coordinate local geocoding result.",
            input_schema=GeocodeInput,
            output_schema=GeocodeOutput,
            responder=lambda request: GeocodeOutput(
                formatted_address=request.address, lat=0, lng=0
            ),
        ),
        FakeTool(
            name="reverse_geocode",
            description="Return a local reverse-geocoding placeholder.",
            input_schema=ReverseGeocodeInput,
            output_schema=GeocodeOutput,
            responder=lambda request: GeocodeOutput(
                formatted_address=f"{request.lat},{request.lng}",
                lat=request.lat,
                lng=request.lng,
            ),
        ),
        FakeTool(
            name="get_route",
            description="Return realistic fake route metrics.",
            input_schema=RouteInput,
            output_schema=RouteOutput,
            responder=lambda _: RouteOutput(distance_m=550000, duration_s=10800),
        ),
        FakeTool(
            name="distance_matrix",
            description="Return zero-distance fake matrix entries.",
            input_schema=DistanceMatrixInput,
            output_schema=DistanceMatrixOutput,
            responder=lambda request: DistanceMatrixOutput(
                elements=[
                    DistanceMatrixElement(
                        origin_index=origin_index,
                        destination_index=destination_index,
                        distance_m=0,
                        duration_s=0,
                        status="FAKE",
                    )
                    for origin_index in range(len(request.origins))
                    for destination_index in range(len(request.destinations))
                ]
            ),
        ),
        FakeTool(
            name="get_forecast",
            description="Return a realistic three-day fake forecast.",
            input_schema=ForecastInput,
            output_schema=ForecastOutput,
            responder=_fake_forecast,
        ),
        FakeTool(
            name="estimate_transport",
            description="Return a clearly labeled fake fare estimate.",
            input_schema=FareEstimateInput,
            output_schema=FareEstimateOutput,
            responder=lambda request: FareEstimateOutput(
                origin=request.origin,
                destination=request.destination,
                mode=request.mode,
                estimated_fare_min=1200,
                estimated_fare_max=2800,
                currency="INR",
                duration_s=10800,
                is_estimate=True,
                source="fake heuristic",
            ),
        ),
    ]
    for tool in tools:
        registry.register(tool)
    return registry
