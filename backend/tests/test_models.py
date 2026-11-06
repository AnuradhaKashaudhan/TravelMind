"""PostgreSQL model, constraints, repository, and schema tests."""

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.repositories.memory import MemoryRepository
from app.db.repositories.trips import TripRepository
from app.db.repositories.versions import TripVersionRepository
from app.models import (
    AgentRun,
    ApprovalRequest,
    BudgetCategory,
    BudgetCategoryType,
    BudgetEntry,
    CollaboratorRole,
    ItineraryDay,
    ItineraryItem,
    ItineraryItemType,
    MemoryType,
    SharedTripLink,
    SharedTripPermission,
    TaskNode,
    Trip,
    TripCollaborator,
    TripMemory,
    TripStatus,
    TripVersion,
    User,
    UserPreference,
)
from app.models.enums import ApprovalActionType
from app.schemas import (
    ApprovalDecision,
    ApprovalRequestCreate,
    TripCreate,
    TripRequirements,
)


async def make_user(session: AsyncSession, email: str = "traveler@example.com") -> User:
    """Add and flush a user for model tests."""
    user = User(email=email, hashed_password="hashed", full_name="Traveler")
    session.add(user)
    await session.flush()
    return user


async def make_trip(session: AsyncSession, user: User) -> Trip:
    """Add and flush a valid trip for model tests."""
    trip = Trip(user_id=user.id, title="City break", destination="Rome")
    session.add(trip)
    await session.flush()
    return trip


@pytest.mark.asyncio
async def test_models_create_relationships_and_cascade_delete(
    db_session: AsyncSession,
) -> None:
    """Persist linked models and cascade user deletion through trip records."""
    user = await make_user(db_session)
    user.preference = UserPreference(
        travel_style="slow", preferred_activities=["museums"]
    )
    trip = await make_trip(db_session, user)
    trip.status = TripStatus.PLANNED
    version = TripVersion(
        trip=trip,
        version_number=1,
        itinerary={},
        budget_snapshot={},
        created_by="system",
    )
    day = ItineraryDay(
        trip_version=version,
        day_number=1,
        date=date(2027, 5, 1),
        summary="Explore the city",
    )
    day.items.append(
        ItineraryItem(
            order_index=0,
            item_type=ItineraryItemType.ACTIVITY,
            title="Visit the forum",
            metadata_={"indoors": False},
        )
    )
    db_session.add(version)
    await db_session.commit()

    assert user.preference is not None
    loaded_trip = await db_session.scalar(
        select(Trip)
        .where(Trip.id == trip.id)
        .options(
            selectinload(Trip.versions)
            .selectinload(TripVersion.days)
            .selectinload(ItineraryDay.items)
        )
    )
    assert loaded_trip is not None
    assert loaded_trip.versions[0].days[0].items[0].title == "Visit the forum"
    await db_session.delete(user)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(Trip)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ItineraryDay)) == 0


@pytest.mark.asyncio
async def test_unique_constraints_reject_duplicate_user_email(
    db_session: AsyncSession,
) -> None:
    """Enforce uniqueness for user email addresses in PostgreSQL."""
    await make_user(db_session)
    db_session.add(
        User(email="traveler@example.com", hashed_password="hash2", full_name="Other")
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_trip_version_repository_allocates_next_version(
    db_session: AsyncSession,
) -> None:
    """Allocate monotonically increasing trip versions in one transaction."""
    trip = await make_trip(db_session, await make_user(db_session))
    repository = TripVersionRepository(db_session)

    first = await repository.create_next_version(trip.id, created_by="system")
    second = await repository.create_next_version(
        trip.id,
        created_by="user",
        change_reason="Updated dates",
        itinerary={"days": []},
    )

    assert (first.version_number, second.version_number) == (1, 2)


@pytest.mark.asyncio
async def test_remaining_domain_models_persist(
    db_session: AsyncSession,
) -> None:
    """Persist run, task, budget, collaboration, and approval records."""
    user = await make_user(db_session)
    trip = await make_trip(db_session, user)
    version = TripVersion(
        trip_id=trip.id,
        version_number=1,
        itinerary={},
        budget_snapshot={},
        created_by="system",
    )
    db_session.add(version)
    await db_session.flush()
    db_session.add_all(
        [
            AgentRun(
                trip_id=trip.id, agent_name="planner", input={"trip": str(trip.id)}
            ),
            TaskNode(trip_id=trip.id, task_key="research", agent_name="researcher"),
            BudgetCategory(
                trip_version_id=version.id,
                category=BudgetCategoryType.FOOD,
                allocated_amount=Decimal("100"),
            ),
            BudgetEntry(
                trip_version_id=version.id,
                category=BudgetCategoryType.FOOD,
                estimated_amount=Decimal("25"),
            ),
            TripCollaborator(
                trip_id=trip.id, user_id=user.id, role=CollaboratorRole.OWNER
            ),
            SharedTripLink(
                trip_id=trip.id,
                token="test-share-token",
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
                permission=SharedTripPermission.VIEWER,
            ),
            ApprovalRequest(
                trip_id=trip.id,
                action_type=ApprovalActionType.PAYMENT,
                payload={"amount": "25.00"},
            ),
        ]
    )

    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(AgentRun)) == 1
    assert await db_session.scalar(select(func.count()).select_from(TaskNode)) == 1
    assert (
        await db_session.scalar(select(func.count()).select_from(ApprovalRequest)) == 1
    )


@pytest.mark.asyncio
async def test_base_repository_crud_and_pagination(
    db_session: AsyncSession,
) -> None:
    """Exercise generic repository creation, lookup, filtering, update, and delete."""
    user = await make_user(db_session)
    repository = TripRepository(db_session)
    trip = await repository.create(
        user_id=user.id,
        title="Repository test",
        destination="Lisbon",
    )
    await db_session.commit()

    assert await repository.get(trip.id) is not None
    assert await repository.list(filters={"user_id": user.id}, limit=1) == [trip]
    updated = await repository.update(trip, {"title": "Updated"})
    assert updated.title == "Updated"
    await repository.delete(updated)
    await db_session.commit()
    assert await repository.get(trip.id) is None


@pytest.mark.asyncio
async def test_pgvector_memory_similarity_search(db_session: AsyncSession) -> None:
    """Order memory records by cosine similarity using the PostgreSQL vector type."""
    user = await make_user(db_session)
    target = [0.0] * 768
    target[0] = 1.0
    near = target.copy()
    near[1] = 0.1
    far = [0.0] * 768
    far[1] = 1.0
    db_session.add_all(
        [
            TripMemory(
                user_id=user.id,
                memory_type=MemoryType.PREFERENCE,
                content="near match",
                embedding=near,
            ),
            TripMemory(
                user_id=user.id,
                memory_type=MemoryType.TRIP,
                content="far match",
                embedding=far,
            ),
        ]
    )
    await db_session.flush()

    results = await MemoryRepository(db_session).similarity_search(
        target, user_id=user.id
    )

    assert [memory.content for memory, _ in results] == ["near match", "far match"]
    assert results[0][1] > results[1][1]


def test_trip_schema_validators() -> None:
    """Reject invalid dates, traveler counts, budgets, and currency formatting."""
    with pytest.raises(ValidationError):
        TripCreate(
            title="Invalid dates",
            destination="Rome",
            start_date=date(2027, 5, 2),
            end_date=date(2027, 5, 1),
        )
    with pytest.raises(ValidationError):
        TripCreate(title="No travelers", destination="Rome", traveler_count=0)
    with pytest.raises(ValidationError):
        TripCreate(title="No budget", destination="Rome", total_budget=Decimal("0"))
    with pytest.raises(ValidationError):
        TripCreate(title="Bad currency", destination="Rome", currency="US")

    requirements = TripRequirements(currency="usd", traveler_count=2)
    assert requirements.currency == "USD"


def test_booking_and_payment_approval_schemas() -> None:
    """Represent booking requests and only accept explicit approval decisions."""
    request = ApprovalRequestCreate(
        trip_id=uuid4(),
        action_type=ApprovalActionType.BOOKING,
        payload={"reservation": "candidate"},
    )
    assert request.action_type == ApprovalActionType.BOOKING
    assert ApprovalDecision(status="approved").status == "approved"
    with pytest.raises(ValidationError):
        ApprovalDecision(status="pending")
