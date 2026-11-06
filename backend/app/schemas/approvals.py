"""Approval request API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.enums import ApprovalActionType, ApprovalStatus
from app.schemas.common import APIModel


class ApprovalRequestCreate(APIModel):
    """Request explicit user approval for a booking or payment action."""

    trip_id: UUID
    action_type: ApprovalActionType
    payload: dict[str, Any]


class ApprovalDecision(APIModel):
    """User decision for a pending approval request."""

    status: ApprovalStatus = Field(
        description="Only approved or rejected decisions are valid."
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: ApprovalStatus) -> ApprovalStatus:
        """Reject statuses that are not explicit user decisions."""
        if value not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ValueError("decision must be approved or rejected")
        return value


class ApprovalRequestRead(APIModel):
    """Persisted approval request representation."""

    id: UUID
    trip_id: UUID
    action_type: ApprovalActionType
    payload: dict[str, Any]
    status: ApprovalStatus
    decided_at: datetime | None
