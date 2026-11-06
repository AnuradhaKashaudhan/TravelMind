"""Shared Pydantic schema configuration and constrained scalar types."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class APIModel(BaseModel):
    """Base schema supporting ORM attribute serialization."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


CurrencyCode = Annotated[str, Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")]


def normalize_currency(value: str) -> str:
    """Normalize a three-letter currency code and reject malformed codes."""
    normalized = value.upper()
    if len(normalized) != 3 or not normalized.isalpha() or not normalized.isascii():
        raise ValueError("currency must be a three-letter ISO 4217 code")
    return normalized


class CurrencyModel(APIModel):
    """Shared uppercase ISO currency validation."""

    @field_validator("currency", mode="before", check_fields=False)
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        """Normalize currency values to uppercase."""
        if value is None:
            return None
        return normalize_currency(value)
