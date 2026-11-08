"""No-network tool stubs selected by ``USE_FAKE_PROVIDERS``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.config import Settings
from app.tools.base import Tool
from app.tools.registry import ToolRegistry
from app.tools.schemas import (
    DistanceMatrixElement,
    DistanceMatrixInput,
    DistanceMatrixOutput,
    FareEstimateInput,
    FareEstimateOutput,
    ForecastInput,
    ForecastOutput,
    GeocodeInput,
    GeocodeOutput,
    PlaceDetailsInput,
    PlaceNearbyInput,
    PlaceOutput,
    PlaceSearchInput,
    PlaceSearchOutput,
    ReverseGeocodeInput,
    RouteInput,
    RouteOutput,
    VerifyPlaceOutput,
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


def build_fake_tool_registry(settings: Settings) -> ToolRegistry:
    """Build the same callable tool surface without making network requests."""
    del settings
    registry = ToolRegistry()
    tools: list[Tool[Any, Any]] = [
        FakeTool(
            name="places_text_search",
            description="Fake text search returning an empty local result set.",
            input_schema=PlaceSearchInput,
            output_schema=PlaceSearchOutput,
            responder=lambda _: PlaceSearchOutput(places=[]),
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
            description="Return a local fake place record.",
            input_schema=PlaceDetailsInput,
            output_schema=PlaceOutput,
            responder=lambda request: PlaceOutput(
                place_id=request.place_id,
                name="Fake Place",
                address="Local test fixture",
                lat=0,
                lng=0,
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
            description="Return zero-distance fake route metrics.",
            input_schema=RouteInput,
            output_schema=RouteOutput,
            responder=lambda _: RouteOutput(distance_m=0, duration_s=0),
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
            description="Return an empty fake forecast.",
            input_schema=ForecastInput,
            output_schema=ForecastOutput,
            responder=lambda _: ForecastOutput(provider="fake", daily=[]),
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
                estimated_fare_min=0,
                estimated_fare_max=0,
                currency="XXX",
                duration_s=0,
                is_estimate=True,
                source="fake",
            ),
        ),
    ]
    for tool in tools:
        registry.register(tool)
    return registry
