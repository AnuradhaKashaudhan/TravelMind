"""Place grounding and per-run de-duplication services."""

from __future__ import annotations

from typing import Any

from app.orchestration.agent_base import AgentContext


class GroundingService:
    """Verify proposed places through the typed Places tool surface."""

    def __init__(self) -> None:
        """Create an empty run-local place cache."""
        self._cache: dict[str, dict[str, Any]] = {}

    async def ground(
        self,
        context: AgentContext,
        items: list[dict[str, Any]],
        *,
        name_key: str = "name",
    ) -> list[dict[str, Any]]:
        """Resolve, verify, enrich, and de-duplicate proposed places."""
        grounded: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            name = str(item.get(name_key) or item.get("title") or "").strip()
            if not name:
                continue
            cache_key = name.casefold()
            if cache_key in seen:
                continue
            seen.add(cache_key)
            record = await self._resolve(context, name)
            if record is None:
                grounded.append(
                    {
                        **item,
                        "verified": False,
                        "verification_note": (
                            "Places verification was unavailable; treat this as "
                            "unverified guidance."
                        ),
                        "source": "llm",
                    }
                )
                continue
            grounded.append({**item, **record, "verified": True, "source": "places"})
        return grounded

    async def _resolve(self, context: AgentContext, name: str) -> dict[str, Any] | None:
        """Resolve one place, returning no record on provider failure or mismatch."""
        key = name.casefold()
        if key in self._cache:
            return self._cache[key]
        try:
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
            verified = await context.tool_registry.get("verify_place").run(
                {"place_id": place.place_id}
            )
            if not verified.exists or not verified.operational:
                return None
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
