"""SQLAlchemy type helpers shared across model definitions."""

from enum import StrEnum
from typing import TypeVar

from sqlalchemy import Enum

EnumType = TypeVar("EnumType", bound=StrEnum)


def enum_column(enum_class: type[EnumType], name: str) -> Enum:
    """Build a database enum that stores stable enum values."""
    return Enum(
        enum_class,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        native_enum=True,
        validate_strings=True,
    )
