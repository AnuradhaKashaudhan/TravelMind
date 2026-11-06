"""User approval records for actions that require explicit consent."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import ApprovalActionType, ApprovalStatus
from app.models.types import enum_column


class ApprovalRequest(TimestampedModel):
    """A booking or payment action awaiting an explicit user decision."""

    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_trip_id", "trip_id"),)

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[ApprovalActionType] = mapped_column(
        enum_column(ApprovalActionType, "approval_action_type"), nullable=False
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_column(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default=ApprovalStatus.PENDING.value,
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trip: Mapped[Trip] = relationship(back_populates="approval_requests")


if TYPE_CHECKING:
    from app.models.trip import Trip
