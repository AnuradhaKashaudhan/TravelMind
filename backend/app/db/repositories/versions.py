"""Trip version repository with per-trip serialized version allocation."""

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.trip import Trip, TripVersion


class TripVersionRepository(BaseRepository[TripVersion]):
    """CRUD operations and atomic version-number allocation."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository for trip versions."""
        super().__init__(session, TripVersion)

    async def create_next_version(
        self,
        trip_id: UUID,
        *,
        itinerary: dict[str, Any] | None = None,
        budget_snapshot: dict[str, Any] | None = None,
        change_reason: str | None = None,
        created_by: str,
    ) -> TripVersion:
        """Lock the parent trip and insert the next version transactionally."""
        trip = await self.session.scalar(
            select(Trip).where(Trip.id == trip_id).with_for_update()
        )
        if trip is None:
            raise NoResultFound(f"trip {trip_id} does not exist")
        current_version = await self.session.scalar(
            select(func.max(TripVersion.version_number)).where(
                TripVersion.trip_id == trip_id
            )
        )
        version = TripVersion(
            trip_id=trip_id,
            version_number=(current_version or 0) + 1,
            itinerary=itinerary or {},
            budget_snapshot=budget_snapshot or {},
            change_reason=change_reason,
            created_by=created_by,
        )
        self.session.add(version)
        await self.session.flush()
        await self.session.refresh(version)
        return version
