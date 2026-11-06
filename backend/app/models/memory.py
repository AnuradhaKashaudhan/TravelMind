"""User and trip memories with vector search support."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import MemoryType
from app.models.types import enum_column


class TripMemory(TimestampedModel):
    """A structured memory associated with a user and optionally a trip."""

    __tablename__ = "trip_memories"
    __table_args__ = (
        Index(
            "ix_trip_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_trip_memories_user_id", "user_id"),
        Index("ix_trip_memories_trip_id", "trip_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    trip_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL")
    )
    memory_type: Mapped[MemoryType] = mapped_column(
        enum_column(MemoryType, "memory_type"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768))

    trip: Mapped[Trip | None] = relationship(back_populates="memories")


if TYPE_CHECKING:
    from app.models.trip import Trip
