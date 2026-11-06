"""Trip and trip-version API schemas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.enums import TravelerType, TripStatus
from app.schemas.common import APIModel, CurrencyModel, normalize_currency


class TripCreate(CurrencyModel):
    """Fields required to create a trip."""

    title: str = Field(min_length=1, max_length=200)
    origin: str | None = Field(default=None, max_length=255)
    destination: str = Field(min_length=1, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    traveler_count: int = Field(default=1, ge=1)
    traveler_type: TravelerType | None = None
    total_budget: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    currency: str = "USD"
    requirements: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dates_are_ordered(self) -> TripCreate:
        """Ensure an end date does not precede the start date."""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

    @field_validator("currency")
    @classmethod
    def currency_is_iso_formatted(cls, value: str) -> str:
        """Normalize the ISO 4217 currency code."""
        return normalize_currency(value)


class TripUpdate(CurrencyModel):
    """Optional mutable trip fields."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: TripStatus | None = None
    origin: str | None = Field(default=None, max_length=255)
    destination: str | None = Field(default=None, min_length=1, max_length=255)
    start_date: date | None = None
    end_date: date | None = None
    traveler_count: int | None = Field(default=None, ge=1)
    traveler_type: TravelerType | None = None
    total_budget: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    currency: str | None = None
    requirements: dict[str, Any] | None = None

    @model_validator(mode="after")
    def dates_are_ordered_when_both_present(self) -> TripUpdate:
        """Validate date ordering when both dates are included in an update."""
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self

    @field_validator("currency")
    @classmethod
    def currency_is_iso_formatted(cls, value: str | None) -> str | None:
        """Normalize optional ISO currency code."""
        return normalize_currency(value) if value is not None else None


class TripRead(APIModel):
    """Persisted trip representation."""

    id: UUID
    user_id: UUID
    title: str
    status: TripStatus
    origin: str | None
    destination: str
    start_date: date | None
    end_date: date | None
    traveler_count: int
    traveler_type: TravelerType | None
    total_budget: Decimal | None
    currency: str
    requirements: dict[str, Any]


class TripVersionCreate(APIModel):
    """Fields for a manually created trip-version snapshot."""

    itinerary: dict[str, Any] = Field(default_factory=dict)
    budget_snapshot: dict[str, Any] = Field(default_factory=dict)
    change_reason: str | None = None
    created_by: str = Field(min_length=1, max_length=100)


class TripVersionRead(APIModel):
    """Persisted trip-version representation."""

    id: UUID
    trip_id: UUID
    version_number: int
    itinerary: dict[str, Any]
    budget_snapshot: dict[str, Any]
    change_reason: str | None
    created_by: str
