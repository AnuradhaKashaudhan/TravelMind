"""Explicit approval pause for tasks requiring booking or payment consent."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from app.models.enums import ApprovalActionType
from app.orchestration.events import EventBus, EventType, WorkflowEvent
from app.schemas.agent_io import TaskSpec


class ApprovalTicket(BaseModel):
    """Approval request envelope kept in graph state and emitted as an event."""

    model_config = ConfigDict(frozen=True)

    approval_id: UUID
    trip_id: UUID
    run_id: UUID
    task_key: str
    action_type: ApprovalActionType
    status: str = "pending"


ApprovalCreator = Callable[
    [ApprovalTicket, dict[str, object]],
    Awaitable[ApprovalTicket],
]
ApprovalDecisionLoader = Callable[[UUID], Awaitable[bool | None]]


class ApprovalGate:
    """Persist approval requests and block execution until explicitly resolved."""

    def __init__(
        self,
        event_bus: EventBus,
        *,
        create_request: ApprovalCreator | None = None,
        load_decision: ApprovalDecisionLoader | None = None,
    ) -> None:
        """Create a gate and optional persistence callback."""
        self.event_bus = event_bus
        self.create_request = create_request
        self.load_decision = load_decision
        self._decisions: dict[UUID, asyncio.Future[bool]] = {}
        self._tickets: dict[tuple[UUID, str], ApprovalTicket] = {}

    async def wait_for_approval(
        self,
        task: TaskSpec,
        *,
        trip_id: UUID,
        run_id: UUID,
        inputs: dict[str, object],
    ) -> bool:
        """Create a pending request and wait for an explicit approval decision."""
        if task.approval_action is None:
            return True
        ticket = ApprovalTicket(
            approval_id=uuid4(),
            trip_id=trip_id,
            run_id=run_id,
            task_key=task.task_key,
            action_type=task.approval_action,
        )
        if self.create_request is not None:
            ticket = await self.create_request(ticket, inputs)
        self._tickets[(run_id, task.task_key)] = ticket
        loop = asyncio.get_running_loop()
        decision: asyncio.Future[bool] = loop.create_future()
        self._decisions[ticket.approval_id] = decision
        if ticket.status == "pending":
            await self.event_bus.publish(
                WorkflowEvent(
                    event_type=EventType.APPROVAL_NEEDED,
                    trip_id=trip_id,
                    run_id=run_id,
                    task_key=task.task_key,
                    payload={
                        "approval_id": str(ticket.approval_id),
                        "action_type": ticket.action_type.value,
                        "status": ticket.status,
                    },
                )
            )
        try:
            if ticket.status != "pending":
                return ticket.status == "approved"
            if self.load_decision is not None:
                while True:
                    if decision.done():
                        return decision.result()
                    persisted = await self.load_decision(ticket.approval_id)
                    if persisted is not None:
                        return persisted
                    try:
                        return await asyncio.wait_for(
                            asyncio.shield(decision),
                            timeout=0.25,
                        )
                    except TimeoutError:
                        continue
            return await decision
        finally:
            self._decisions.pop(ticket.approval_id, None)

    def resolve(self, approval_id: UUID, approved: bool) -> None:
        """Resolve an active approval wait; unknown or duplicate IDs fail clearly."""
        decision = self._decisions.get(approval_id)
        if decision is None:
            raise KeyError(f"No active approval request: {approval_id}")
        if decision.done():
            raise ValueError(f"Approval request is already resolved: {approval_id}")
        decision.set_result(approved)

    def is_waiting(self, approval_id: UUID) -> bool:
        """Return whether this process currently has a waiter for the request."""
        return approval_id in self._decisions

    def requests_for_run(self, run_id: UUID) -> dict[str, UUID]:
        """Return approval IDs created or reused for one run."""
        return {
            task_key: ticket.approval_id
            for (ticket_run_id, task_key), ticket in self._tickets.items()
            if ticket_run_id == run_id
        }
