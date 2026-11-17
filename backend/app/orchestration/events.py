"""Transport-neutral typed event stream for planning runs."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventType(StrEnum):
    """Public lifecycle event names emitted by the orchestration engine."""

    RUN_STARTED = "run_started"
    TASK_STARTED = "task_started"
    TASK_SUCCEEDED = "task_succeeded"
    TASK_FAILED = "task_failed"
    TASK_RETRYING = "task_retrying"
    TASK_SKIPPED = "task_skipped"
    CLARIFICATION_NEEDED = "clarification_needed"
    APPROVAL_NEEDED = "approval_needed"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"


class WorkflowEvent(BaseModel):
    """Typed event with stable run identity and UTC occurrence time."""

    model_config = ConfigDict(frozen=True)

    event_type: EventType
    trip_id: UUID
    run_id: UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    task_key: str | None = None
    payload: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class EventBus(Protocol):
    """Asynchronous event publication and subscription interface."""

    async def publish(self, event: WorkflowEvent) -> None:
        """Publish one event to active subscribers."""

    def subscribe(self) -> AsyncIterator[WorkflowEvent]:
        """Yield events published after the subscription is created."""


class InMemoryEventBus:
    """Fan out events to independent asynchronous subscriber queues."""

    def __init__(self) -> None:
        """Create an event bus with no subscribers."""
        self._subscribers: set[asyncio.Queue[WorkflowEvent | None]] = set()
        self.events: list[WorkflowEvent] = []

    async def publish(self, event: WorkflowEvent) -> None:
        """Publish an event to all currently active subscribers."""
        self.events.append(event)
        for subscriber in tuple(self._subscribers):
            await subscriber.put(event)

    async def subscribe(self) -> AsyncIterator[WorkflowEvent]:
        """Yield events until the consumer closes the iterator."""
        queue: asyncio.Queue[WorkflowEvent | None] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._subscribers.discard(queue)
