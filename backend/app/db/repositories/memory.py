"""Memory repository, including pgvector cosine similarity search."""

from collections.abc import Sequence
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.memory import TripMemory


class MemoryRepository(BaseRepository[TripMemory]):
    """CRUD and cosine similarity search for stored trip memories."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository for trip memories."""
        super().__init__(session, TripMemory)

    async def similarity_search(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        user_id: UUID | None = None,
        trip_id: UUID | None = None,
    ) -> list[tuple[TripMemory, float]]:
        """Return nearest memories as (record, cosine-similarity) pairs."""
        if len(embedding) != 768:
            raise ValueError("embedding must contain exactly 768 dimensions")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        distance = TripMemory.embedding.cosine_distance(list(embedding))
        statement = select(
            TripMemory,
            distance.label("distance"),
        ).where(TripMemory.embedding.is_not(None))
        if user_id is not None:
            statement = statement.where(TripMemory.user_id == user_id)
        if trip_id is not None:
            statement = statement.where(TripMemory.trip_id == trip_id)
        result = await self.session.execute(statement.order_by(distance).limit(limit))
        rows = cast(
            Sequence[tuple[TripMemory, float]],
            result.all(),
        )
        return [
            (memory, float(1 - cosine_distance)) for memory, cosine_distance in rows
        ]
