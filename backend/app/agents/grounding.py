"""Place grounding and per-run de-duplication services."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.orchestration.agent_base import AgentContext


class GroundingService:
    """Verify proposed places through the typed Places tool surface."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Create an empty run-local place cache and bounded concurrency."""
        self._cache: dict[str, dict[str, Any]] = {}
        self._concurrency = (settings or Settings()).grounding_concurrency

    async def ground(
        self,
        context: AgentContext,
        items: list[dict[str, Any]],
        *,
        name_key: str = "name",
    ) -> list[dict[str, Any]]:
        """Resolve, verify, enrich, and de-duplicate proposed places."""
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            name = str(item.get(name_key) or item.get("title") or "").strip()
            if not name:
                continue
            cache_key = name.casefold()
            if cache_key in seen:
                continue
            seen.add(cache_key)
            unique.append(item)
        semaphore = asyncio.Semaphore(self._concurrency)

        async def resolve(item: dict[str, Any]) -> dict[str, Any]:
            name = str(item.get(name_key) or item.get("title") or "").strip()
            record = await self._resolve(context, name, semaphore)
            if record is None:
                status = await self._verification_status(context, name, semaphore)
                return {
                    **item,
                    "verified": False,
                    "verification_status": status,
                    "verification_note": (
                        "Place not found in Places; treat this as a likely "
                        "hallucination."
                        if status == "not_found"
                        else "Place verification unavailable; treat this as "
                        "unverified guidance."
                    ),
                    "source": "llm",
                }
            return {
                **item,
                **record,
                "verified": True,
                "verification_status": "verified",
                "verification_note": None,
                "source": "places",
            }

        return list(await asyncio.gather(*(resolve(item) for item in unique)))

    async def _resolve(
        self,
        context: AgentContext,
        name: str,
        semaphore: asyncio.Semaphore,
    ) -> dict[str, Any] | None:
        """Resolve one place, returning no record on provider failure or mismatch."""
        key = name.casefold()
        if key in self._cache:
            return self._cache[key]
        try:
            async with semaphore:
                search = await context.tool_registry.get("places_text_search").run(
                    {"query": name, "max_results": 5}
                )
            places = getattr(search, "places", [])
            place = next(
                (candidate for candidate in places if candidate.name.casefold() == key),
                places[0] if places else None,
            )
            if place is None:
                return None
            async with semaphore:
                verified = await context.tool_registry.get("verify_place").run(
                    {"place_id": place.place_id}
                )
            if not verified.exists or not verified.operational:
                return None
            async with semaphore:
                details = await context.tool_registry.get("place_details").run(
                    {"place_id": place.place_id}
                )
            record = {
                "name": details.name,
                "place_id": details.place_id,
                "latitude": details.lat,
                "longitude": details.lng,
                "rating": details.rating,
                "opening_hours": details.opening_hours,
                "price_level": details.price_level,
                "types": details.types,
            }
            self._cache[key] = record
            return record
        except Exception as error:
            context.logger.warning(
                "place_grounding_degraded",
                place_name=name,
                error_type=type(error).__name__,
            )
            return None

    async def _verification_status(
        self,
        context: AgentContext,
        name: str,
        semaphore: asyncio.Semaphore,
    ) -> str:
        """Distinguish a successful empty search from provider unavailability."""
        try:
            async with semaphore:
                search = await context.tool_registry.get("places_text_search").run(
                    {"query": name, "max_results": 5}
                )
        except Exception:
            return "unavailable"
        return "not_found" if not getattr(search, "places", []) else "unavailable"
