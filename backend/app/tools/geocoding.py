"""Google Geocoding API address and coordinate conversion tools."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field, SecretStr

from app.core.config import get_settings
from app.core.exceptions import ToolError
from app.tools.base import Tool
from app.tools.google_maps import GoogleMapsClient
from app.tools.schemas import (
    GeocodeInput,
    GeocodeOutput,
    ReverseGeocodeInput,
)


class _GoogleLatLng(BaseModel):
    """Google Geocoding coordinate object."""

    lat: float
    lng: float


class _GoogleGeometry(BaseModel):
    """Google Geocoding result geometry."""

    location: _GoogleLatLng


class _GoogleGeocodeResult(BaseModel):
    """Subset of a Google Geocoding result."""

    formatted_address: str
    geometry: _GoogleGeometry
    place_id: str | None = None
    types: list[str] = Field(default_factory=list)


class _GoogleGeocodeResponse(BaseModel):
    """Google Geocoding response envelope."""

    status: str
    results: list[_GoogleGeocodeResult] = Field(default_factory=list)
    error_message: str | None = None


class _GoogleGeocodeTool(Tool[Any, GeocodeOutput]):
    """Shared Google Geocoding endpoint behavior."""

    provider = "google_geocoding"
    output_schema = GeocodeOutput

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a geocoding tool with the configured cache duration."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().geocoding_cache_ttl_s)

    def _normalize(self, result: _GoogleGeocodeResult) -> GeocodeOutput:
        """Convert Google result fields to the normalized contract."""
        return GeocodeOutput(
            formatted_address=result.formatted_address,
            lat=result.geometry.location.lat,
            lng=result.geometry.location.lng,
            place_id=result.place_id,
            types=result.types,
        )

    def _first_result(self, response_data: object) -> _GoogleGeocodeResult:
        """Validate the response and require a first geocoding result."""
        response = _GoogleGeocodeResponse.model_validate(response_data)
        if response.status != "OK" or not response.results:
            raise ToolError(f"Google Geocoding returned no result ({response.status})")
        return response.results[0]


class GeocodeTool(_GoogleGeocodeTool):
    """Convert an address to geographic coordinates."""

    name = "geocode"
    description = "Convert an address into latitude and longitude."
    input_schema = GeocodeInput

    async def _run(self, payload: GeocodeInput) -> GeocodeOutput:
        """Call Google Geocoding and normalize its first result."""
        params = self.maps.authenticated_params({"address": payload.address})
        response = await self.maps.http.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params=params,
        )
        response.raise_for_status()
        return self._normalize(self._first_result(response.json()))


class ReverseGeocodeTool(_GoogleGeocodeTool):
    """Convert coordinates to a formatted address."""

    name = "reverse_geocode"
    description = "Convert latitude and longitude into a formatted address."
    input_schema = ReverseGeocodeInput

    async def _run(self, payload: ReverseGeocodeInput) -> GeocodeOutput:
        """Call Google reverse geocoding and normalize its first result."""
        params = self.maps.authenticated_params(
            {"latlng": f"{payload.lat},{payload.lng}"}
        )
        response = await self.maps.http.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params=params,
        )
        response.raise_for_status()
        return self._normalize(self._first_result(response.json()))


class GeocodingTools:
    """Facade exposing geocode and reverse_geocode as named methods."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create both geocoding operations over a shared Google client."""
        maps = GoogleMapsClient(api_key, http_client=http_client)
        self.geocode_tool = GeocodeTool(maps=maps)
        self.reverse_geocode_tool = ReverseGeocodeTool(maps=maps)

    async def geocode(self, address: str) -> GeocodeOutput:
        """Convert an address to coordinates."""
        return await self.geocode_tool.run({"address": address})

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodeOutput:
        """Convert coordinates to a formatted address."""
        return await self.reverse_geocode_tool.run({"lat": lat, "lng": lng})
