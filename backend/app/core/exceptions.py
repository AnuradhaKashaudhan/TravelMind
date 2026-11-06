"""Application exception types and their HTTP representations."""

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


class TravelMindError(Exception):
    """Base class for expected application errors."""

    status_code = 500
    error_code = "travelmind_error"

    def __init__(self, message: str) -> None:
        """Initialize an error with a safe, user-facing message."""
        super().__init__(message)
        self.message = message


class AgentError(TravelMindError):
    """An agent execution failed."""

    error_code = "agent_error"


class ToolError(TravelMindError):
    """A travel planning tool failed."""

    status_code = 502
    error_code = "tool_error"


class ValidationFailure(TravelMindError):
    """Application-level input validation failed."""

    status_code = 422
    error_code = "validation_failure"


class ExternalServiceError(TravelMindError):
    """An external service could not complete a request."""

    status_code = 503
    error_code = "external_service_error"


async def travelmind_error_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Convert expected application errors into a consistent JSON response."""
    if not isinstance(exc, TravelMindError):
        raise TypeError("TravelMind error handler received an unexpected exception")
    logger.warning(
        "application_error",
        error_code=exc.error_code,
        path=request.url.path,
        message=exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error_code, "detail": exc.message},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers for the application exception hierarchy."""
    app.add_exception_handler(TravelMindError, travelmind_error_handler)
