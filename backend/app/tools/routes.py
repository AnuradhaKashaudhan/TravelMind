"""Google Routes API route and distance-matrix tools."""

from __future__ import annotations

import re

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import get_settings
from app.tools.base import Tool
from app.tools.google_maps import GoogleMapsClient
from app.tools.schemas import (
    DistanceMatrixElement,
    DistanceMatrixInput,
    DistanceMatrixOutput,
    RouteInput,
    RouteOutput,
)

_FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
_MATRIX_FIELD_MASK = (
    "originIndex,destinationIndex,duration,distanceMeters,status,condition"
)


class _GooglePolyline(BaseModel):
    """Encoded Google route polyline."""

    encoded_polyline: str | None = Field(default=None, alias="encodedPolyline")
    model_config = ConfigDict(populate_by_name=True)


class _GoogleRoute(BaseModel):
    """Google Routes API route metrics."""

    distance_meters: int = Field(alias="distanceMeters")
    duration: str
    polyline: _GooglePolyline | None = None
    model_config = ConfigDict(populate_by_name=True)


class _GoogleRouteResponse(BaseModel):
    """Google Routes API response envelope."""

    routes: list[_GoogleRoute] = Field(default_factory=list)


class _GoogleStatus(BaseModel):
    """Google API status object returned per matrix entry."""

    code: int | str = 0
    message: str | None = None


class _GoogleMatrixElement(BaseModel):
    """Google route matrix element."""

    origin_index: int = Field(alias="originIndex")
    destination_index: int = Field(alias="destinationIndex")
    duration: str | None = None
    distance_meters: int | None = Field(default=None, alias="distanceMeters")
    status: _GoogleStatus | str | None = None
    condition: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class RouteTool(Tool[RouteInput, RouteOutput]):
    """Get distance, duration, and encoded polyline for a route."""

    name = "get_route"
    description = "Get a normalized route between two places."
    input_schema = RouteInput
    output_schema = RouteOutput
    provider = "google_routes"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a route tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().routes_cache_ttl_s)

    async def _run(self, payload: RouteInput) -> RouteOutput:
        """Call Routes API and normalize its first route."""
        response = await self.maps.http.post(
            "https://routes.googleapis.com/directions/v2:computeRoutes",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": _FIELD_MASK,
            },
            json={
                "origin": _waypoint(payload.origin),
                "destination": _waypoint(payload.destination),
                "travelMode": _travel_mode(payload.mode),
                "polylineEncoding": "ENCODED_POLYLINE",
            },
        )
        response.raise_for_status()
        result = _GoogleRouteResponse.model_validate(response.json())
        if not result.routes:
            raise ValueError("Google Routes returned no route")
        route = result.routes[0]
        return RouteOutput(
            distance_m=route.distance_meters,
            duration_s=_duration_seconds(route.duration),
            polyline=route.polyline.encoded_polyline if route.polyline else None,
        )


class DistanceMatrixTool(Tool[DistanceMatrixInput, DistanceMatrixOutput]):
    """Compute distances and travel durations for multiple places."""

    name = "distance_matrix"
    description = "Get travel distances and durations for point pairs."
    input_schema = DistanceMatrixInput
    output_schema = DistanceMatrixOutput
    provider = "google_routes"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a distance matrix tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().routes_cache_ttl_s)

    async def _run(self, payload: DistanceMatrixInput) -> DistanceMatrixOutput:
        """Call Routes computeRouteMatrix and normalize each matrix element."""
        response = await self.maps.http.post(
            "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": _MATRIX_FIELD_MASK,
            },
            json={
                "origins": [{"waypoint": _waypoint(item)} for item in payload.origins],
                "destinations": [
                    {"waypoint": _waypoint(item)} for item in payload.destinations
                ],
                "travelMode": _travel_mode(payload.mode),
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            raise ValueError("Google Routes returned an invalid distance matrix")
        elements = [
            _normalize_matrix_element(_GoogleMatrixElement.model_validate(item))
            for item in data
        ]
        return DistanceMatrixOutput(elements=elements)


class RoutesTools:
    """Facade exposing route and distance-matrix helper methods."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create both route operations over a shared Google client."""
        maps = GoogleMapsClient(api_key, http_client=http_client)
        self.route_tool = RouteTool(maps=maps)
        self.distance_matrix_tool = DistanceMatrixTool(maps=maps)

    async def get_route(
        self,
        origin: str,
        destination: str,
        mode: str = "driving",
    ) -> RouteOutput:
        """Get a route by address or coordinate string."""
        return await self.route_tool.run(
            {"origin": origin, "destination": destination, "mode": mode}
        )

    async def distance_matrix(
        self,
        origins: list[str],
        destinations: list[str],
        mode: str = "driving",
    ) -> DistanceMatrixOutput:
        """Get distances and durations for sets of points."""
        return await self.distance_matrix_tool.run(
            {"origins": origins, "destinations": destinations, "mode": mode}
        )


def _waypoint(location: str) -> dict[str, object]:
    """Interpret a comma-separated numeric pair as a lat/lng waypoint."""
    parts = [part.strip() for part in location.split(",")]
    if len(parts) == 2:
        try:
            latitude, longitude = float(parts[0]), float(parts[1])
        except ValueError:
            pass
        else:
            return {
                "location": {"latLng": {"latitude": latitude, "longitude": longitude}}
            }
    return {"address": location}


def _travel_mode(mode: str) -> str:
    """Map the stable tool mode names to Google Routes API enums."""
    return {
        "driving": "DRIVE",
        "walking": "WALK",
        "transit": "TRANSIT",
        "bicycling": "BICYCLE",
    }[mode]


def _duration_seconds(duration: str) -> int:
    """Parse Google's seconds-suffixed duration."""
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", duration)
    if match is None:
        raise ValueError(f"Invalid route duration: {duration}")
    return round(float(match.group(1)))


def _normalize_matrix_element(
    element: _GoogleMatrixElement,
) -> DistanceMatrixElement:
    """Convert a provider route matrix entry to the stable output."""
    status: object = element.condition or "UNKNOWN"
    if element.status is not None:
        code = (
            element.status.code
            if isinstance(element.status, _GoogleStatus)
            else element.status
        )
        status = element.condition if code in {0, "0", "OK"} else code
    return DistanceMatrixElement(
        origin_index=element.origin_index,
        destination_index=element.destination_index,
        distance_m=element.distance_meters,
        duration_s=(_duration_seconds(element.duration) if element.duration else None),
        status=str(status),
    )
