"""Trip collaboration and expiring shared links."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import CollaboratorRole, SharedTripPermission
from app.models.types import enum_column


class TripCollaborator(TimestampedModel):
    """A user's role on a shared trip."""

    __tablename__ = "trip_collaborators"
    __table_args__ = (
        UniqueConstraint("trip_id", "user_id", name="uq_trip_collaborators_trip_user"),
    )

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[CollaboratorRole] = mapped_column(
        enum_column(CollaboratorRole, "collaborator_role"), nullable=False
    )

    trip: Mapped[Trip] = relationship(back_populates="collaborators")
    user: Mapped[User] = relationship()


class SharedTripLink(TimestampedModel):
    """An expiring link that grants limited access to a trip."""

    __tablename__ = "shared_trip_links"
    __table_args__ = (Index("ix_shared_trip_links_trip_id", "trip_id"),)

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    permission: Mapped[SharedTripPermission] = mapped_column(
        enum_column(SharedTripPermission, "shared_trip_permission"),
        default=SharedTripPermission.VIEWER,
        server_default=SharedTripPermission.VIEWER.value,
        nullable=False,
    )

    trip: Mapped[Trip] = relationship(back_populates="shared_links")


if TYPE_CHECKING:
    from app.models.trip import Trip
    from app.models.user import User
