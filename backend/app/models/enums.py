"""Shared domain enums for persistence and API contracts."""

from enum import StrEnum


class UserRole(StrEnum):
    """Application roles assigned to users."""

    USER = "user"
    ADMIN = "admin"


class TripStatus(StrEnum):
    """Lifecycle states for a trip."""

    DRAFT = "draft"
    PLANNING = "planning"
    PLANNED = "planned"
    REPLANNING = "replanning"
    FAILED = "failed"
    ARCHIVED = "archived"


class TravelerType(StrEnum):
    """Types of travel parties."""

    SOLO = "solo"
    COUPLE = "couple"
    FAMILY = "family"
    STUDENT = "student"
    GROUP = "group"
    BUSINESS = "business"


class ItineraryItemType(StrEnum):
    """Kinds of itinerary entries."""

    ACTIVITY = "activity"
    MEAL = "meal"
    TRANSPORT = "transport"
    ACCOMMODATION = "accommodation"
    FREE_TIME = "free_time"


class BudgetCategoryType(StrEnum):
    """Travel budget categories."""

    TRANSPORTATION = "transportation"
    ACCOMMODATION = "accommodation"
    FOOD = "food"
    ACTIVITIES = "activities"
    MISCELLANEOUS = "miscellaneous"


class AgentRunStatus(StrEnum):
    """Execution statuses for agent runs and task nodes."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class MemoryType(StrEnum):
    """Kinds of memories retained for later planning."""

    PREFERENCE = "preference"
    TRIP = "trip"
    EXECUTION = "execution"


class CollaboratorRole(StrEnum):
    """Permissions for a trip collaborator."""

    OWNER = "owner"
    EDITOR = "editor"
    VIEWER = "viewer"


class SharedTripPermission(StrEnum):
    """Permissions granted by a shared trip link."""

    VIEWER = "viewer"
    EDITOR = "editor"


class ApprovalActionType(StrEnum):
    """Actions requiring explicit user authorization."""

    BOOKING = "booking"
    PAYMENT = "payment"


class ApprovalStatus(StrEnum):
    """Approval request decision states."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
