"""User and preference API request and response schemas."""

from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from app.models.enums import UserRole
from app.schemas.common import APIModel


class UserCreate(APIModel):
    """Fields required to register a user."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


class UserUpdate(APIModel):
    """Mutable user profile fields."""

    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class UserRead(APIModel):
    """Public user representation."""

    id: UUID
    email: EmailStr
    full_name: str
    is_active: bool
    role: UserRole


class UserPreferenceCreate(APIModel):
    """Fields for creating a user's travel preferences."""

    travel_style: str | None = Field(default=None, max_length=100)
    food_preferences: dict[str, Any] = Field(default_factory=dict)
    accommodation_preferences: dict[str, Any] = Field(default_factory=dict)
    accessibility_needs: dict[str, Any] = Field(default_factory=dict)
    preferred_activities: list[str] = Field(default_factory=list)


class UserPreferenceUpdate(APIModel):
    """Optional updates to travel preferences."""

    travel_style: str | None = Field(default=None, max_length=100)
    food_preferences: dict[str, Any] | None = None
    accommodation_preferences: dict[str, Any] | None = None
    accessibility_needs: dict[str, Any] | None = None
    preferred_activities: list[str] | None = None


class UserPreferenceRead(UserPreferenceCreate):
    """Persisted preference representation without the internal embedding."""

    id: UUID
    user_id: UUID
