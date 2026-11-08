"""Validated input and normalized output contracts for external tools."""

from __future__ import annotations

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolModel(BaseModel):
    """Base schema that rejects unexpected provider-facing parameters."""

    model_config = ConfigDict(extra="forbid")


class Place(ToolModel):
    """Normalized Google Places record."""

    place_id: str
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    rating: float | None = None
    user_ratings_total: int | None = None
    price_level: str | None = None
    opening_hours: list[str] | None = None
    types: list[str] = Field(default_factory=list)
    website: str | None = None
    operational: bool | None = None


class PlaceSearchInput(ToolModel):
    """Text search request."""

    query: str = Field(min_length=1, max_length=500)
    language_code: str | None = "en"
    region_code: str | None = "IN"
    max_results: int = Field(default=10, ge=1, le=20)


class PlaceNearbyInput(ToolModel):
    """Nearby search request centered at a coordinate."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    radius_m: float = Field(default=5000, gt=0, le=50000)
    included_types: list[str] = Field(default_factory=list)
    max_results: int = Field(default=10, ge=1, le=20)


class PlaceDetailsInput(ToolModel):
    """Place detail lookup request."""

    place_id: str = Field(min_length=1)


class PlaceSearchOutput(ToolModel):
    """List of normalized place results."""

    places: list[Place]


class PlaceOutput(Place):
    """Place detail response."""


class VerifyPlaceOutput(ToolModel):
    """Operational verification result."""

    place_id: str
    exists: bool
    operational: bool


class GeocodeInput(ToolModel):
    """Address-to-coordinate request."""

    address: str = Field(min_length=1, max_length=500)


class ReverseGeocodeInput(ToolModel):
    """Coordinate-to-address request."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class GeocodeOutput(ToolModel):
    """Normalized geocoding result."""

    formatted_address: str
    lat: float
    lng: float
    place_id: str | None = None
    types: list[str] = Field(default_factory=list)


TransportMode = Literal["driving", "walking", "transit", "bicycling"]


class RouteInput(ToolModel):
    """Single route request."""

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    mode: TransportMode = "driving"


class RouteOutput(ToolModel):
    """Normalized route metrics."""

    distance_m: int
    duration_s: int
    polyline: str | None = None


class DistanceMatrixInput(ToolModel):
    """Distance matrix request with bounded point counts."""

    origins: list[str] = Field(min_length=1, max_length=25)
    destinations: list[str] = Field(min_length=1, max_length=25)
    mode: TransportMode = "driving"


class DistanceMatrixElement(ToolModel):
    """One origin/destination matrix entry."""

    origin_index: int
    destination_index: int
    distance_m: int | None = None
    duration_s: int | None = None
    status: str


class DistanceMatrixOutput(ToolModel):
    """Normalized distance matrix."""

    elements: list[DistanceMatrixElement]


class ForecastInput(ToolModel):
    """Date-bounded forecast request."""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def validate_date_range(self) -> Self:
        """Require an ordered forecast date range."""
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class DailyForecast(ToolModel):
    """Normalized single-day weather forecast."""

    date: date
    temp_min_c: float
    temp_max_c: float
    precipitation_probability: float = Field(ge=0, le=100)
    precipitation_mm: float = Field(ge=0)
    wind_speed_kmh: float = Field(ge=0)
    condition_code: int


class WeatherRisk(ToolModel):
    """A detected weather risk with severity."""

    kind: Literal["heavy_rain", "extreme_heat", "extreme_cold", "storm"]
    severity: Literal["low", "moderate", "high"]
    date: date
    message: str


class ForecastOutput(ToolModel):
    """Daily forecast and derived weather risks."""

    provider: str
    daily: list[DailyForecast]
    risks: list[WeatherRisk] = Field(default_factory=list)


FareMode = Literal["taxi", "public_transport", "flight", "train"]


class FareEstimateInput(ToolModel):
    """Transport estimation input; fare results are explicitly estimates."""

    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    mode: FareMode
    distance_m: float = Field(gt=0)


class FareEstimateOutput(ToolModel):
    """Clearly labeled configured fare estimate."""

    origin: str
    destination: str
    mode: FareMode
    estimated_fare_min: float
    estimated_fare_max: float
    currency: str
    duration_s: int
    is_estimate: bool = True
    source: str
