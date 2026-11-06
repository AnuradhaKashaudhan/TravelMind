"""FastAPI application factory and foundational endpoints."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import bind_contextvars, clear_contextvars, configure_logging
from app.db.session import engine

logger = structlog.get_logger(__name__)
VERSION = "0.1.0"


async def check_database() -> None:
    """Verify that PostgreSQL can execute a trivial query."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def check_redis(request: Request) -> None:
    """Verify that the application Redis client responds to a ping."""
    redis_client: Redis = request.app.state.redis
    await redis_client.ping()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Connect infrastructure resources on startup and release them on shutdown."""
    settings = get_settings()
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    app.state.redis = redis_client
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        await redis_client.ping()
        logger.info("infrastructure_connected")
        yield
    finally:
        await redis_client.aclose()
        await engine.dispose()
        logger.info("infrastructure_disconnected")


def create_app() -> FastAPI:
    """Create and configure the TravelMind API application."""
    configure_logging()
    settings = get_settings()
    application = FastAPI(
        title="TravelMind API",
        version=VERSION,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(application)

    @application.middleware("http")
    async def request_context_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Attach request and trace identifiers to logs and response headers."""
        request_id = request.headers.get("x-request-id") or str(uuid4())
        trace_id = request.headers.get("x-trace-id") or request_id
        bind_contextvars(request_id=request_id, trace_id=trace_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            clear_contextvars()

    @application.get("/health", tags=["system"])
    async def health(request: Request) -> JSONResponse:
        """Report PostgreSQL and Redis availability."""
        components: dict[str, str] = {"database": "healthy", "redis": "healthy"}
        try:
            await check_database()
        except SQLAlchemyError:
            components["database"] = "unhealthy"
            logger.warning("database_health_check_failed")
        try:
            await check_redis(request)
        except RedisError:
            components["redis"] = "unhealthy"
            logger.warning("redis_health_check_failed")

        healthy = all(status == "healthy" for status in components.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "healthy" if healthy else "unhealthy",
                "components": components,
            },
        )

    @application.get("/version", tags=["system"])
    async def version() -> dict[str, str]:
        """Return service identity and runtime environment."""
        return {
            "name": "TravelMind API",
            "version": VERSION,
            "environment": settings.environment,
        }

    return application


app = create_app()
