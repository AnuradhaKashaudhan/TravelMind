"""Trips, versions, and their itinerary records."""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TimestampedModel
from app.models.enums import (
    BudgetCategoryType,
    ItineraryItemType,
    TravelerType,
    TripStatus,
)
from app.models.types import enum_column


class Trip(TimestampedModel):
    """A user's travel plan and its high-level requirements."""

    __tablename__ = "trips"
    __table_args__ = (
        Index("ix_trips_user_id", "user_id"),
        CheckConstraint("traveler_count >= 1", name="ck_trips_traveler_count_positive"),
        CheckConstraint(
            "total_budget IS NULL OR total_budget > 0",
            name="ck_trips_budget_positive",
        ),
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_trips_dates_ordered",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[TripStatus] = mapped_column(
        enum_column(TripStatus, "trip_status"),
        default=TripStatus.DRAFT,
        server_default=TripStatus.DRAFT.value,
        nullable=False,
    )
    origin: Mapped[str | None] = mapped_column(String(255))
    destination: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    traveler_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    traveler_type: Mapped[TravelerType | None] = mapped_column(
        enum_column(TravelerType, "traveler_type")
    )
    total_budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="trips")
    versions: Mapped[list[TripVersion]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    task_nodes: Mapped[list[TaskNode]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    memories: Mapped[list[TripMemory]] = relationship(back_populates="trip")
    collaborators: Mapped[list[TripCollaborator]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    shared_links: Mapped[list[SharedTripLink]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )
    approval_requests: Mapped[list[ApprovalRequest]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )


class TripVersion(TimestampedModel):
    """An immutable numbered snapshot of a trip plan."""

    __tablename__ = "trip_versions"
    __table_args__ = (
        UniqueConstraint(
            "trip_id", "version_number", name="uq_trip_versions_trip_version"
        ),
        CheckConstraint(
            "version_number >= 1", name="ck_trip_versions_version_positive"
        ),
    )

    trip_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    itinerary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    budget_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, nullable=False
    )
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)

    trip: Mapped[Trip] = relationship(back_populates="versions")
    days: Mapped[list[ItineraryDay]] = relationship(
        back_populates="trip_version", cascade="all, delete-orphan"
    )
    budget_categories: Mapped[list[BudgetCategory]] = relationship(
        back_populates="trip_version", cascade="all, delete-orphan"
    )
    budget_entries: Mapped[list[BudgetEntry]] = relationship(
        back_populates="trip_version", cascade="all, delete-orphan"
    )


class ItineraryDay(TimestampedModel):
    """One day within a specific trip version."""

    __tablename__ = "itinerary_days"
    __table_args__ = (
        UniqueConstraint(
            "trip_version_id", "day_number", name="uq_itinerary_days_version_day"
        ),
        CheckConstraint("day_number >= 1", name="ck_itinerary_days_day_positive"),
    )

    trip_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("trip_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)

    trip_version: Mapped[TripVersion] = relationship(back_populates="days")
    items: Mapped[list[ItineraryItem]] = relationship(
        back_populates="day", cascade="all, delete-orphan"
    )


class ItineraryItem(TimestampedModel):
    """An ordered activity, meal, or other itinerary entry."""

    __tablename__ = "itinerary_items"
    __table_args__ = (
        UniqueConstraint("day_id", "order_index", name="uq_itinerary_items_day_order"),
        CheckConstraint(
            "order_index >= 0", name="ck_itinerary_items_order_nonnegative"
        ),
        CheckConstraint(
            "estimated_cost IS NULL OR estimated_cost >= 0",
            name="ck_itinerary_items_cost_nonnegative",
        ),
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="ck_itinerary_items_latitude_range",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="ck_itinerary_items_longitude_range",
        ),
    )

    day_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("itinerary_days.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[ItineraryItemType] = mapped_column(
        enum_column(ItineraryItemType, "itinerary_item_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    start_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    end_time: Mapped[time | None] = mapped_column(Time(timezone=False))
    place_id: Mapped[str | None] = mapped_column(String(255))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, default=dict, nullable=False
    )

    day: Mapped[ItineraryDay] = relationship(back_populates="items")


class BudgetCategory(TimestampedModel):
    """A planned allocation for one budget category in a trip version."""

    __tablename__ = "budget_categories"
    __table_args__ = (
        UniqueConstraint(
            "trip_version_id",
            "category",
            name="uq_budget_categories_version_category",
        ),
        CheckConstraint(
            "allocated_amount >= 0",
            name="ck_budget_categories_allocation_nonnegative",
        ),
    )

    trip_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("trip_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[BudgetCategoryType] = mapped_column(
        enum_column(BudgetCategoryType, "budget_category_type"), nullable=False
    )
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    trip_version: Mapped[TripVersion] = relationship(back_populates="budget_categories")


class BudgetEntry(TimestampedModel):
    """An estimated cost entry assigned to a budget category."""

    __tablename__ = "budget_entries"
    __table_args__ = (
        Index("ix_budget_entries_trip_version_id", "trip_version_id"),
        CheckConstraint(
            "estimated_amount >= 0",
            name="ck_budget_entries_amount_nonnegative",
        ),
    )

    trip_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("trip_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[BudgetCategoryType] = mapped_column(
        enum_column(BudgetCategoryType, "budget_category_type"), nullable=False
    )
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    trip_version: Mapped[TripVersion] = relationship(back_populates="budget_entries")


if TYPE_CHECKING:
    from app.models.execution import AgentRun, TaskNode
    from app.models.memory import TripMemory
    from app.models.safety import ApprovalRequest
    from app.models.sharing import SharedTripLink, TripCollaborator
    from app.models.user import User
