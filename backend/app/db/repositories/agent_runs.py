"""Agent run repository."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.execution import AgentRun


class AgentRunRepository(BaseRepository[AgentRun]):
    """CRUD operations for agent run records."""

    def __init__(self, session: AsyncSession) -> None:
        """Initialize the repository for agent runs."""
        super().__init__(session, AgentRun)
