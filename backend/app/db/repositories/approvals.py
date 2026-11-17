"""Repository for approval requests created by orchestration gates."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.enums import ApprovalStatus
from app.models.safety import ApprovalRequest


class ApprovalRequestRepository(BaseRepository[ApprovalRequest]):
    """Read and decide approval requests without owning commits."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a caller-owned async session."""
        super().__init__(session, ApprovalRequest)

    async def decide(
        self,
        request: ApprovalRequest,
        status: ApprovalStatus,
    ) -> ApprovalRequest:
        """Record a terminal user decision and its UTC timestamp."""
        if status not in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}:
            raise ValueError("Approval decision must be approved or rejected")
        return await self.update(
            request,
            {"status": status, "decided_at": datetime.now(UTC)},
        )
