"""User accounts and travel preferences."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import UserRole
from app.models.types import enum_column

if TYPE_CHECKING:
    from app.models.trip import Trip


class User(TimestampedModel):
    """A TravelMind account."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, "user_role"),
        default=UserRole.USER,
        server_default=UserRole.USER.value,
        nullable=False,
    )

    preference: Mapped[UserPreference | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    trips: Mapped[list[Trip]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserPreference(TimestampedModel):
    """Structured preferences and optional embedding for a user."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        Index(
            "ix_user_preferences_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    travel_style: Mapped[str | None] = mapped_column(String(100))
    food_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    accommodation_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    accessibility_needs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    preferred_activities: Mapped[list[str]] = mapped_column(
        JSONB, default=list, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    user: Mapped[User] = relationship(back_populates="preference")
