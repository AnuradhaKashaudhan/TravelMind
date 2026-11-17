"""Persistence helpers for run-scoped task graph nodes."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.enums import AgentRunStatus
from app.models.execution import TaskNode


class TaskNodeRepository(BaseRepository[TaskNode]):
    """Create and update task nodes within the caller's transaction."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to an existing async session."""
        super().__init__(session, TaskNode)

    async def get_by_trip_and_key(
        self,
        trip_id: UUID,
        task_key: str,
    ) -> TaskNode | None:
        """Look up one run-scoped task node by its persisted key."""
        result = await self.session.scalars(
            select(TaskNode).where(
                TaskNode.trip_id == trip_id,
                TaskNode.task_key == task_key,
            )
        )
        return result.first()

    async def list_by_trip(self, trip_id: UUID) -> list[TaskNode]:
        """List all task nodes for a trip in stable key order."""
        result = await self.session.scalars(
            select(TaskNode)
            .where(TaskNode.trip_id == trip_id)
            .order_by(TaskNode.task_key)
        )
        return list(result.all())

    async def upsert(
        self,
        *,
        trip_id: UUID,
        task_key: str,
        agent_name: str,
        depends_on: list[str],
        priority: int,
        status: AgentRunStatus,
        checkpoint: dict[str, object],
    ) -> TaskNode:
        """Insert or update a task node without committing the session."""
        node = await self.get_by_trip_and_key(trip_id, task_key)
        values: dict[str, object] = {
            "agent_name": agent_name,
            "depends_on": depends_on,
            "priority": priority,
            "status": status,
            "checkpoint": checkpoint,
        }
        if node is None:
            return await self.create(
                trip_id=trip_id,
                task_key=task_key,
                **values,
            )
        return await self.update(node, values)
