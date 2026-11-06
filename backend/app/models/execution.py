"""Agent run and task graph persistence records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import AgentRunStatus
from app.models.types import enum_column


class AgentRun(TimestampedModel):
    """A recorded execution of one agent for a trip."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_trip_id", "trip_id"),
        Index("ix_agent_runs_supervisor_run_id", "supervisor_run_id"),
        CheckConstraint("retry_count >= 0", name="ck_agent_runs_retry_nonnegative"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_agent_runs_duration_nonnegative",
        ),
    )

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    supervisor_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus, "agent_run_status"),
        default=AgentRunStatus.PENDING,
        server_default=AgentRunStatus.PENDING.value,
        nullable=False,
    )
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    trip: Mapped[Trip] = relationship(back_populates="agent_runs")
    supervisor_run: Mapped[AgentRun | None] = relationship(
        remote_side="AgentRun.id", back_populates="child_runs"
    )
    child_runs: Mapped[list[AgentRun]] = relationship(back_populates="supervisor_run")


class TaskNode(TimestampedModel):
    """A planned task node and its dependencies within a trip run."""

    __tablename__ = "task_nodes"
    __table_args__ = (
        CheckConstraint("priority >= 0", name="ck_task_nodes_priority_nonnegative"),
        UniqueConstraint("trip_id", "task_key", name="uq_task_nodes_trip_task_key"),
    )

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    task_key: Mapped[str] = mapped_column(String(160), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    depends_on: Mapped[list[str]] = mapped_column(
        ARRAY(String(160)), default=list, nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus, "agent_run_status"),
        default=AgentRunStatus.PENDING,
        server_default=AgentRunStatus.PENDING.value,
        nullable=False,
    )
    checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    trip: Mapped[Trip] = relationship(back_populates="task_nodes")


if TYPE_CHECKING:
    from app.models.trip import Trip
