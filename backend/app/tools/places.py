"""Google Places API (New) search, detail, and operational verification tools."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import get_settings
from app.tools.base import Tool
from app.tools.google_maps import GoogleMapsClient
from app.tools.schemas import (
    Place,
    PlaceDetailsInput,
    PlaceNearbyInput,
    PlaceOutput,
    PlaceSearchInput,
    PlaceSearchOutput,
    VerifyPlaceOutput,
)

PLACES_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,"
    "places.rating,places.userRatingCount,places.priceLevel,"
    "places.regularOpeningHours.weekdayDescriptions,places.types,"
    "places.websiteUri,places.businessStatus"
)
PLACE_FIELD_MASK = (
    "id,displayName,formattedAddress,location,rating,userRatingCount,priceLevel,"
    "regularOpeningHours.weekdayDescriptions,types,websiteUri,businessStatus"
)


class _GoogleLocation(BaseModel):
    """Google Places coordinate representation."""

    latitude: float | None = None
    longitude: float | None = None


class _GoogleDisplayName(BaseModel):
    """Localized Google place display name."""

    text: str | None = None


class _GoogleOpeningHours(BaseModel):
    """Google regular opening hours."""

    weekday_descriptions: list[str] = Field(
        default_factory=list, alias="weekdayDescriptions"
    )


class _GooglePlace(BaseModel):
    """Subset of a Google Places API response used by normalization."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: _GoogleDisplayName | None = Field(
        default=None, alias="displayName"
    )
    formatted_address: str | None = Field(default=None, alias="formattedAddress")
    location: _GoogleLocation | None = None
    rating: float | None = None
    user_ratings_total: int | None = Field(default=None, alias="userRatingCount")
    price_level: str | None = Field(default=None, alias="priceLevel")
    regular_opening_hours: _GoogleOpeningHours | None = Field(
        default=None, alias="regularOpeningHours"
    )
    types: list[str] = Field(default_factory=list)
    website_uri: str | None = Field(default=None, alias="websiteUri")
    business_status: str | None = Field(default=None, alias="businessStatus")


class _GooglePlaceSearchResponse(BaseModel):
    """Google Places search response."""

    places: list[_GooglePlace] = Field(default_factory=list)


def _normalize_place(place: _GooglePlace) -> Place:
    """Convert provider place fields to the stable TravelMind schema."""
    location = place.location
    display_name = place.display_name
    return Place(
        place_id=place.id,
        name=display_name.text if display_name and display_name.text else "",
        address=place.formatted_address,
        lat=location.latitude if location else None,
        lng=location.longitude if location else None,
        rating=place.rating,
        user_ratings_total=place.user_ratings_total,
        price_level=place.price_level,
        opening_hours=(
            place.regular_opening_hours.weekday_descriptions
            if place.regular_opening_hours
            else None
        ),
        types=place.types,
        website=place.website_uri,
        operational=(
            place.business_status == "OPERATIONAL"
            if place.business_status is not None
            else None
        ),
    )


class TextSearchTool(Tool[PlaceSearchInput, PlaceSearchOutput]):
    """Search places with a natural-language query."""

    name = "places_text_search"
    description = "Search Google Places using a text query."
    input_schema = PlaceSearchInput
    output_schema = PlaceSearchOutput
    provider = "google_places"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a Places text search tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().places_cache_ttl_s)

    async def _run(self, payload: PlaceSearchInput) -> PlaceSearchOutput:
        response = await self.maps.http.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": PLACES_FIELD_MASK,
            },
            json={
                "textQuery": payload.query,
                "languageCode": payload.language_code,
                "regionCode": payload.region_code,
                "pageSize": payload.max_results,
            },
        )
        response.raise_for_status()
        result = _GooglePlaceSearchResponse.model_validate(response.json())
        return PlaceSearchOutput(
            places=[_normalize_place(place) for place in result.places]
        )


class NearbySearchTool(Tool[PlaceNearbyInput, PlaceSearchOutput]):
    """Search for places around a coordinate and radius."""

    name = "places_nearby_search"
    description = "Find places near a coordinate using Google Places."
    input_schema = PlaceNearbyInput
    output_schema = PlaceSearchOutput
    provider = "google_places"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a Places nearby search tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().places_cache_ttl_s)

    async def _run(self, payload: PlaceNearbyInput) -> PlaceSearchOutput:
        body: dict[str, Any] = {
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": payload.lat, "longitude": payload.lng},
                    "radius": payload.radius_m,
                }
            },
            "maxResultCount": payload.max_results,
        }
        if payload.included_types:
            body["includedTypes"] = payload.included_types
        response = await self.maps.http.post(
            "https://places.googleapis.com/v1/places:searchNearby",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": PLACES_FIELD_MASK,
            },
            json=body,
        )
        response.raise_for_status()
        result = _GooglePlaceSearchResponse.model_validate(response.json())
        return PlaceSearchOutput(
            places=[_normalize_place(place) for place in result.places]
        )


class PlaceDetailsTool(Tool[PlaceDetailsInput, PlaceOutput]):
    """Retrieve normalized detail for a known Google place ID."""

    name = "place_details"
    description = "Get Google Places details by place ID."
    input_schema = PlaceDetailsInput
    output_schema = PlaceOutput
    provider = "google_places"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a place details tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().places_cache_ttl_s)

    async def _run(self, payload: PlaceDetailsInput) -> PlaceOutput:
        response = await self.maps.http.get(
            f"https://places.googleapis.com/v1/places/{payload.place_id}",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": PLACE_FIELD_MASK,
            },
        )
        response.raise_for_status()
        normalized = _normalize_place(_GooglePlace.model_validate(response.json()))
        return PlaceOutput.model_validate(normalized.model_dump())


class VerifyPlaceTool(Tool[PlaceDetailsInput, VerifyPlaceOutput]):
    """Confirm that a place exists and is currently operational."""

    name = "verify_place"
    description = "Verify that a Google place exists and is operational."
    input_schema = PlaceDetailsInput
    output_schema = VerifyPlaceOutput
    provider = "google_places"

    def __init__(
        self,
        *,
        maps: GoogleMapsClient | None = None,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create an operational verification tool."""
        self.maps = maps or GoogleMapsClient(api_key, http_client=http_client)
        super().__init__(cache_ttl_s=get_settings().places_cache_ttl_s)

    async def _run(self, payload: PlaceDetailsInput) -> VerifyPlaceOutput:
        response = await self.maps.http.get(
            f"https://places.googleapis.com/v1/places/{payload.place_id}",
            headers={
                **self.maps.headers(),
                "X-Goog-FieldMask": "id,businessStatus",
            },
        )
        if response.status_code == 404:
            return VerifyPlaceOutput(
                place_id=payload.place_id, exists=False, operational=False
            )
        response.raise_for_status()
        place = _GooglePlace.model_validate(response.json())
        operational = place.business_status == "OPERATIONAL"
        return VerifyPlaceOutput(
            place_id=place.id,
            exists=True,
            operational=operational,
        )


class PlacesTools:
    """Convenient facade exposing the requested named Places operations."""

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a facade sharing a single Google HTTP client."""
        maps = GoogleMapsClient(api_key, http_client=http_client)
        self.text_search_tool = TextSearchTool(maps=maps)
        self.nearby_search_tool = NearbySearchTool(maps=maps)
        self.place_details_tool = PlaceDetailsTool(maps=maps)
        self.verify_place_tool = VerifyPlaceTool(maps=maps)

    async def text_search(self, query: str, max_results: int = 10) -> PlaceSearchOutput:
        """Search places by text."""
        return await self.text_search_tool.run(
            {"query": query, "max_results": max_results}
        )

    async def nearby_search(
        self,
        lat: float,
        lng: float,
        radius_m: float = 5000,
        max_results: int = 10,
    ) -> PlaceSearchOutput:
        """Search for nearby places."""
        return await self.nearby_search_tool.run(
            {
                "lat": lat,
                "lng": lng,
                "radius_m": radius_m,
                "max_results": max_results,
            }
        )

    async def get_place_details(self, place_id: str) -> PlaceOutput:
        """Get normalized details for a place ID."""
        return await self.place_details_tool.run({"place_id": place_id})

    async def verify_place(self, place_id: str) -> VerifyPlaceOutput:
        """Verify that a place exists and remains operational."""
        return await self.verify_place_tool.run({"place_id": place_id})
