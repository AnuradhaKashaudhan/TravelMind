"""Generic async CRUD repository."""

from collections.abc import Mapping
from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import TimestampedModel

ModelT = TypeVar("ModelT", bound=TimestampedModel)


class BaseRepository(Generic[ModelT]):
    """Provide common async CRUD operations for mapped entities."""

    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        """Bind the repository to an async session and model type."""
        self.session = session
        self.model = model

    async def get(self, entity_id: UUID) -> ModelT | None:
        """Fetch one entity by UUID."""
        return await self.session.get(self.model, entity_id)

    async def list(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        filters: Mapping[str, object] | None = None,
    ) -> list[ModelT]:
        """List entities with equality filters and bounded pagination."""
        if offset < 0:
            raise ValueError("offset must be nonnegative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        statement = select(self.model)
        if filters:
            for field_name, value in filters.items():
                if field_name not in self.model.__table__.columns:
                    raise ValueError(
                        f"unknown {self.model.__name__} field: {field_name}"
                    )
                statement = statement.where(getattr(self.model, field_name) == value)
        result = await self.session.scalars(statement.offset(offset).limit(limit))
        return list(result.all())

    async def create(self, **values: object) -> ModelT:
        """Add and flush a new entity without committing the caller's transaction."""
        entity = self.model(**values)
        self.session.add(entity)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def update(
        self,
        entity: ModelT,
        values: Mapping[str, object],
    ) -> ModelT:
        """Update mapped columns and flush within the current transaction."""
        column_names = set(self.model.__table__.columns.keys())
        invalid_fields = set(values) - column_names
        if invalid_fields:
            raise ValueError(
                f"unknown {self.model.__name__} fields: {sorted(invalid_fields)}"
            )
        for field_name, value in values.items():
            setattr(entity, field_name, value)
        await self.session.flush()
        await self.session.refresh(entity)
        return entity

    async def delete(self, entity: ModelT) -> None:
        """Delete an entity within the current transaction."""
        await self.session.delete(entity)
        await self.session.flush()
