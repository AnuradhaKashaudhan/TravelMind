"""Structured JSON logging and request context helpers."""

import logging

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars

from app.core.config import get_settings


def configure_logging() -> None:
    """Configure structlog to emit JSON with contextual request fields."""
    logging.basicConfig(
        format="%(message)s",
        level=get_settings().log_level.upper(),
        force=True,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(get_settings().log_level.upper())
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


__all__ = ["bind_contextvars", "clear_contextvars", "configure_logging"]
