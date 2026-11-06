"""Tests for foundational health and version endpoints."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import Request

from app import main


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    """Create an HTTP client with infrastructure checks isolated from services."""

    async def healthy_database() -> None:
        return None

    async def healthy_redis(request: Request) -> None:
        return None

    monkeypatch.setattr(main, "check_database", healthy_database)
    monkeypatch.setattr(main, "check_redis", healthy_redis)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_reports_healthy_components(
    client: httpx.AsyncClient,
) -> None:
    """Return a successful health result when both dependencies respond."""
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "components": {"database": "healthy", "redis": "healthy"},
    }


@pytest.mark.asyncio
async def test_version_reports_service_metadata(client: httpx.AsyncClient) -> None:
    """Return the service name, version, and configured environment."""
    response = await client.get("/version")

    assert response.status_code == 200
    assert response.json() == {
        "name": "TravelMind API",
        "version": main.VERSION,
        "environment": "development",
    }
