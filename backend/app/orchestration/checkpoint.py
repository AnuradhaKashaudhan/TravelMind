"""PostgreSQL checkpointer lifecycle for resumable planning graphs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def postgres_checkpointer(db_url: str) -> AsyncIterator[Any]:
    """Open and initialize an AsyncPostgresSaver for an async SQLAlchemy URL."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    psycopg_url = db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with AsyncPostgresSaver.from_conn_string(psycopg_url) as saver:
        await saver.setup()
        yield saver
