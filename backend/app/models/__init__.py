"""Import all mapped entities so metadata is complete for migrations."""

from app.db.base import Base
from app.models.enums import (
    ApprovalActionType,
    ApprovalStatus,
    BudgetCategoryType,
    CollaboratorRole,
    ItineraryItemType,
    MemoryType,
    SharedTripPermission,
    TravelerType,
    TripStatus,
    UserRole,
)
from app.models.execution import AgentRun, TaskNode
from app.models.memory import TripMemory
from app.models.safety import ApprovalRequest
from app.models.sharing import SharedTripLink, TripCollaborator
from app.models.trip import (
    BudgetCategory,
    BudgetEntry,
    ItineraryDay,
    ItineraryItem,
    Trip,
    TripVersion,
)
from app.models.user import User, UserPreference

__all__ = [
    "AgentRun",
    "ApprovalActionType",
    "ApprovalRequest",
    "ApprovalStatus",
    "BudgetCategory",
    "BudgetCategoryType",
    "BudgetEntry",
    "Base",
    "CollaboratorRole",
    "ItineraryDay",
    "ItineraryItem",
    "ItineraryItemType",
    "MemoryType",
    "SharedTripLink",
    "SharedTripPermission",
    "TaskNode",
    "TravelerType",
    "Trip",
    "TripCollaborator",
    "TripMemory",
    "TripStatus",
    "TripVersion",
    "User",
    "UserPreference",
    "UserRole",
]
