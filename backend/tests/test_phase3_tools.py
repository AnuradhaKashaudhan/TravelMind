"""Offline tests for typed tool parsing, caching, and fare estimation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis

import app.tools.base as tool_base
from app.core.cache import RedisCache
from app.core.resilience import AsyncRateLimiter, CircuitBreaker
from app.tools.base import Tool
from app.tools.geocoding import GeocodeTool, ReverseGeocodeTool
from app.tools.google_maps import GoogleMapsClient
from app.tools.places import (
    NearbySearchTool,
    PlaceDetailsTool,
    TextSearchTool,
    VerifyPlaceTool,
)
from app.tools.routes import DistanceMatrixTool, RouteTool
from app.tools.schemas import (
    PlaceSearchInput,
    PlaceSearchOutput,
)
from app.tools.transport_estimator import (
    HeuristicFareProvider,
    TransportEstimatorTool,
)
from app.tools.weather import WeatherTool

FIXTURES = Path(__file__).parent / "fixtures"


class _MemoryLock:
    """Minimal async lock implementing the Redis lock methods used by tools."""

    def __init__(self, lock: asyncio.Lock) -> None:
        self.lock = lock

    async def acquire(self) -> bool:
        await self.lock.acquire()
        return True

    async def release(self) -> None:
        self.lock.release()


class _MemoryRedis:
    """Small in-memory Redis test double with shared per-key locks."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int) -> bool:
        del ex
        self.values[key] = value
        return True

    async def delete(self, key: str) -> int:
        return int(self.values.pop(key, None) is not None)

    def lock(self, key: str, **kwargs: object) -> _MemoryLock:
        del kwargs
        return _MemoryLock(self.locks.setdefault(key, asyncio.Lock()))


class _CounterTool(Tool[PlaceSearchInput, PlaceSearchOutput]):
    """Counting tool used to test cache-aside and stampede protection."""

    name = "counter"
    description = "Count test calls."
    input_schema = PlaceSearchInput
    output_schema = PlaceSearchOutput
    provider = "counter_test"

    def __init__(self) -> None:
        self.calls = 0
        super().__init__(
            cache_ttl_s=60,
            limiter=AsyncRateLimiter(1000, 100),
            breaker=CircuitBreaker(5, 1),
        )

    async def _run(self, payload: PlaceSearchInput) -> PlaceSearchOutput:
        del payload
        self.calls += 1
        await asyncio.sleep(0.01)
        return PlaceSearchOutput(places=[])


def _install_memory_cache(monkeypatch: pytest.MonkeyPatch) -> RedisCache:
    """Inject in-memory cache and high-capacity provider controls."""
    cache = RedisCache(cast(Redis, _MemoryRedis()))
    limiter = AsyncRateLimiter(1000, 100)
    breaker = CircuitBreaker(100, 1)
    monkeypatch.setattr(tool_base, "_cache", lambda: cache)
    monkeypatch.setattr(tool_base, "_limiter", lambda _: limiter)
    monkeypatch.setattr(tool_base, "_breaker", lambda _: breaker)
    return cache


def _fixture(name: str) -> dict[str, object] | list[object]:
    """Read one recorded JSON API response fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


async def test_tool_cache_hit_and_stampede_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Identical concurrent tool requests share one provider execution."""
    _install_memory_cache(monkeypatch)
    tool = _CounterTool()
    payload = {"query": "Manali cafes"}
    first, second = await asyncio.gather(tool.run(payload), tool.run(payload))
    assert first == second
    assert tool.calls == 1
    assert await tool.run(payload) == first
    assert tool.calls == 1


async def test_maps_tools_parse_and_normalize_recorded_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Places, geocoding, routes, and matrices normalize mocked HTTP responses."""
    _install_memory_cache(monkeypatch)
    place_result = _fixture("places_search.json")
    geocode_result = _fixture("geocode.json")
    route_result = _fixture("route.json")
    matrix_result = _fixture("route_matrix.json")

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/geocode/json"):
            assert request.headers.get("X-Goog-Api-Key") == "test-key"
        if request.url.path.endswith("places:searchText") or request.url.path.endswith(
            "places:searchNearby"
        ):
            return httpx.Response(200, json=place_result)
        if request.url.path.endswith("/places/ChIJfake001"):
            return httpx.Response(200, json=place_result["places"][0])
        if request.url.path.endswith("/geocode/json"):
            assert request.url.params.get("key") == "test-key"
            return httpx.Response(200, json=geocode_result)
        if request.url.path.endswith("directions/v2:computeRoutes"):
            return httpx.Response(200, json=route_result)
        if request.url.path.endswith("distanceMatrix/v2:computeRouteMatrix"):
            return httpx.Response(200, json=matrix_result)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    maps = GoogleMapsClient(SecretStr("test-key"), http_client=client)
    text = await TextSearchTool(maps=maps).run({"query": "cafes in Manali"})
    assert text.places[0].name == "Himalayan Cafe"
    assert text.places[0].lat == pytest.approx(32.2396)
    assert text.places[0].operational is True
    nearby = await NearbySearchTool(maps=maps).run(
        {"lat": 32.2396, "lng": 77.1887, "radius_m": 1000}
    )
    assert nearby.places[0].place_id == "ChIJfake001"
    details = await PlaceDetailsTool(maps=maps).run({"place_id": "ChIJfake001"})
    assert details.website == "https://example.test/"
    verified = await VerifyPlaceTool(maps=maps).run({"place_id": "ChIJfake001"})
    assert verified.exists and verified.operational

    geocoded = await GeocodeTool(maps=maps).run({"address": "Manali"})
    reversed_result = await ReverseGeocodeTool(maps=maps).run(
        {"lat": 32.2396, "lng": 77.1887}
    )
    assert geocoded.place_id == "ChIJmanali"
    assert reversed_result.formatted_address == "Manali, Himachal Pradesh, India"

    route = await RouteTool(maps=maps).run(
        {"origin": "Manali", "destination": "Kullu", "mode": "driving"}
    )
    assert route.distance_m == 41100
    assert route.duration_s == 3960
    assert route.polyline == "u~btA_fake_polyline"
    matrix = await DistanceMatrixTool(maps=maps).run(
        {"origins": ["Manali"], "destinations": ["Kullu", "Naggar"]}
    )
    assert matrix.elements[0].distance_m == 41100
    assert matrix.elements[1].status == "ROUTE_NOT_FOUND"
    await client.aclose()


async def test_weather_normalization_and_risk_flagging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open-Meteo data becomes daily models and expected travel risks."""
    _install_memory_cache(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.open-meteo.com"
        return httpx.Response(200, json=_fixture("open_meteo.json"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    tool = WeatherTool(open_meteo_client=client)
    result = await tool.run(
        {
            "lat": 32.2,
            "lng": 77.1,
            "start_date": "2026-11-09",
            "end_date": "2026-11-10",
        }
    )
    assert result.provider == "open_meteo"
    assert result.daily[0].temp_max_c == 38.5
    risk_kinds = {risk.kind for risk in result.risks}
    assert {"heavy_rain", "extreme_heat", "extreme_cold", "storm"} <= risk_kinds
    await client.aclose()


async def test_weather_uses_configured_openweather_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OpenWeatherMap is used only when primary forecast requests fail."""
    _install_memory_cache(monkeypatch)

    def primary_handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(503)

    def fallback_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("appid") == "test-key"
        return httpx.Response(200, json=_fixture("open_weather.json"))

    primary = httpx.AsyncClient(
        transport=httpx.MockTransport(primary_handler),
    )
    fallback = httpx.AsyncClient(
        base_url="https://api.openweathermap.org/data/2.5",
        transport=httpx.MockTransport(fallback_handler),
    )
    tool = WeatherTool(
        open_meteo_client=primary,
        openweather_client=fallback,
        openweather_api_key=SecretStr("test-key"),
    )
    result = await tool.run(
        {
            "lat": 32.2,
            "lng": 77.1,
            "start_date": "2026-11-09",
            "end_date": "2026-11-09",
        }
    )
    assert result.provider == "openweathermap"
    assert result.daily[0].precipitation_probability == 25
    await primary.aclose()
    await fallback.aclose()


async def test_transport_estimator_is_explicitly_an_estimate() -> None:
    """Configured fare heuristics return a range, duration, and estimate label."""
    config_path = Path(__file__).parents[1] / "app" / "tools" / "fare_estimates.json"
    provider = HeuristicFareProvider.from_file(config_path)
    tool = TransportEstimatorTool(provider=provider)
    result = await tool.run(
        {
            "origin": "Manali",
            "destination": "Kullu",
            "mode": "taxi",
            "distance_m": 41100,
        }
    )
    assert result.is_estimate is True
    assert result.source == "configured_heuristic"
    assert result.estimated_fare_min <= result.estimated_fare_max
    assert result.duration_s > 0
