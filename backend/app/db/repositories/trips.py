"""Trip repository."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.trip import Trip


class TripRepository(BaseRepository[Trip]):
    """CRUD operations for trips."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository for trips."""
        super().__init__(session, Trip)
