"""Public API and agent I/O schemas."""

from app.schemas.agent_io import (
    AccommodationOutput,
    ActivityOutput,
    AgentResult,
    BudgetReport,
    DestinationResearchOutput,
    ItineraryPlan,
    LocalIntelligenceOutput,
    SupervisorPlan,
    TaskSpec,
    TransportOutput,
    TripRequirements,
    ValidationReport,
    WeatherOutput,
)
from app.schemas.approvals import (
    ApprovalDecision,
    ApprovalRequestCreate,
    ApprovalRequestRead,
)
from app.schemas.trips import TripCreate, TripRead, TripUpdate, TripVersionRead
from app.schemas.users import (
    UserCreate,
    UserPreferenceCreate,
    UserPreferenceRead,
    UserPreferenceUpdate,
    UserRead,
    UserUpdate,
)

__all__ = [
    "AccommodationOutput",
    "ActivityOutput",
    "AgentResult",
    "ApprovalDecision",
    "ApprovalRequestCreate",
    "ApprovalRequestRead",
    "BudgetReport",
    "DestinationResearchOutput",
    "ItineraryPlan",
    "LocalIntelligenceOutput",
    "SupervisorPlan",
    "TaskSpec",
    "TransportOutput",
    "TripCreate",
    "TripRead",
    "TripRequirements",
    "TripUpdate",
    "TripVersionRead",
    "UserCreate",
    "UserPreferenceCreate",
    "UserPreferenceRead",
    "UserPreferenceUpdate",
    "UserRead",
    "UserUpdate",
    "ValidationReport",
    "WeatherOutput",
]
