"""Task-scoped approval context for tools tagged as booking or payment actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field

from app.core.exceptions import ToolError
from app.models.enums import ApprovalActionType

ToolApprovalHandler = Callable[
    [ApprovalActionType, str, dict[str, object]],
    Awaitable[bool],
]


@dataclass
class ToolApprovalContext:
    """Authorization state scoped to one agent task's async execution."""

    handler: ToolApprovalHandler
    approved_actions: set[ApprovalActionType] = field(default_factory=set)


_CURRENT_APPROVAL_CONTEXT: ContextVar[ToolApprovalContext | None] = ContextVar(
    "travelmind_tool_approval_context",
    default=None,
)


@contextmanager
def tool_approval_context(
    handler: ToolApprovalHandler,
    approved_actions: set[ApprovalActionType] | None = None,
) -> Iterator[None]:
    """Bind the orchestration approval gate for the current agent task."""
    context = ToolApprovalContext(handler, approved_actions or set())
    token: Token[ToolApprovalContext | None] = _CURRENT_APPROVAL_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_APPROVAL_CONTEXT.reset(token)


async def require_tool_approval(
    action: ApprovalActionType,
    tool_name: str,
    payload: dict[str, object],
) -> None:
    """Pause a tagged tool call until the task-scoped gate approves it."""
    context = _CURRENT_APPROVAL_CONTEXT.get()
    if context is None:
        raise ToolError(f"{tool_name} requires explicit orchestration approval")
    if action in context.approved_actions:
        return
    if not await context.handler(action, tool_name, payload):
        raise ToolError(f"{tool_name} approval was rejected")
    context.approved_actions.add(action)
