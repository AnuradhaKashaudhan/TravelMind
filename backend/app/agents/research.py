"""The six Phase 5 specialist research agents."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.agents.base_research import ResearchAgent
from app.agents.grounding import GroundingService
from app.orchestration.agent_base import AgentContext
from app.schemas.agent_io import (
    AccommodationOption,
    AccommodationOutput,
    ActivityOption,
    ActivityOutput,
    AgentResult,
    DestinationResearchOutput,
    LocalIntelligenceOutput,
    TransportOption,
    TransportOutput,
    TripRequirements,
    WeatherDay,
    WeatherOutput,
)
from app.tools.schemas import ForecastOutput, GeocodeOutput


def _requirements_from(context: AgentContext) -> TripRequirements:
    """Return requirements for agents whose state is serialized."""
    value = context.state.get("requirements")
    return TripRequirements.model_validate(value or context.task.inputs)


class DestinationResearchAgent(ResearchAgent[DestinationResearchOutput]):
    """Research and ground a destination's main areas and attractions."""

    name = "destination"
    description = "Grounded destination overview, attractions, hidden gems, and areas."
    output_model = DestinationResearchOutput
    prompt_name = "destination_research_v1.txt"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a grounded destination research result."""
        requirements = _requirements_from(context)
        destination = requirements.destination or context.task.inputs.get(
            "destination", ""
        )
        geo = await self.call_tool(context, "geocode", {"address": destination})
        try:
            output: DestinationResearchOutput = await self.generate(
                context, requirements
            )
        except Exception as error:
            output = DestinationResearchOutput(
                destination=destination,
                overview=(
                    "Destination research is unavailable; verify details "
                    "before booking."
                ),
            )
            output = output.model_copy(
                update={"sources": ["llm-unavailable"], "confidence": 0}
            )
            return self.envelope(
                output, errors=[f"LLM degraded: {type(error).__name__}"]
            )
        if isinstance(geo, GeocodeOutput):
            output = output.model_copy(
                update={"latitude": geo.lat, "longitude": geo.lng}
            )
        grounding = GroundingService()
        popular = await grounding.ground(
            context, output.popular_attractions or output.highlights
        )
        hidden = await grounding.ground(context, output.hidden_gems)
        output = output.model_copy(
            update={"popular_attractions": popular, "hidden_gems": hidden}
        )
        verified = sum(int(item.get("verified", False)) for item in popular + hidden)
        total = len(popular + hidden)
        confidence = verified / total if total else 0.4
        return self.envelope(
            output, sources=["geocode", "places"], confidence=confidence
        )


class TransportAgent(ResearchAgent[TransportOutput]):
    """Estimate intercity and local transport without presenting live fares."""

    name = "transport"
    description = "Route options and clearly labeled transport estimates."
    output_model = TransportOutput
    prompt_name = "transport_research_v1.txt"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return route and labeled fare estimates."""
        requirements = _requirements_from(context)
        destination = requirements.destination or ""
        origin = requirements.origin or ""
        route = await self.call_tool(
            context, "get_route", {"origin": origin, "destination": destination}
        )
        distance = float(getattr(route, "distance_m", 0)) / 1000
        duration = int(getattr(route, "duration_s", 0) / 60)
        try:
            output: TransportOutput = await self.generate(context, requirements)
        except Exception:
            output = TransportOutput()
        modes = ("flight", "train", "bus", "car", "taxi")
        fare_semaphore = asyncio.Semaphore(3)

        async def estimate(mode: str) -> TransportOption:
            """Estimate one mode while bounding concurrent provider calls."""
            fare_mode = (
                mode if mode in {"flight", "train", "taxi"} else "public_transport"
            )
            async with fare_semaphore:
                fare = await self.call_tool(
                    context,
                    "estimate_transport",
                    {
                        "origin": origin,
                        "destination": destination,
                        "mode": fare_mode,
                        "distance_m": max(distance, 1),
                    },
                )
            return TransportOption(
                mode=mode,
                description=f"{mode.title()} estimate; not a live fare.",
                duration_minutes=getattr(fare, "duration_s", duration) // 60,
                distance_km=distance,
                estimated_cost_min=Decimal(str(getattr(fare, "estimated_fare_min", 0))),
                estimated_cost_max=Decimal(str(getattr(fare, "estimated_fare_max", 0))),
                currency=getattr(fare, "currency", requirements.currency),
                is_estimate=True,
                source=getattr(fare, "source", "heuristic"),
                verified=False,
                verification_status="skipped",
                verification_note="Estimate only; confirm live availability and price.",
            )

        options = list(await asyncio.gather(*(estimate(mode) for mode in modes)))
        selected = output.recommended_mode or (
            "train" if requirements.travel_style == "comfort" else "bus"
        )
        return self.envelope(
            output.model_copy(
                update={"options": options, "recommended_mode": selected}
            ),
            sources=["get_route", "estimate_transport"],
            confidence=0.6 if route is not None else 0.2,
        )


class PricingProvider(Protocol):
    """Pluggable accommodation pricing contract."""

    def estimate(self, price_level: str | None) -> tuple[Decimal, Decimal]:
        """Return a minimum and maximum nightly estimate."""


class HeuristicPricingProvider:
    """Default price-level estimator used until a hotel API is integrated."""

    _levels = {
        "1": (20, 50),
        "2": (45, 90),
        "3": (80, 160),
        "4": (140, 280),
        "5": (250, 500),
    }

    def estimate(self, price_level: str | None) -> tuple[Decimal, Decimal]:
        """Estimate nightly cost from the normalized Places price level."""
        low, high = self._levels.get(str(price_level), (40, 120))
        return Decimal(low), Decimal(high)


class AccommodationAgent(ResearchAgent[AccommodationOutput]):
    """Rank grounded lodging candidates against budget and preferences."""

    name = "accommodation"
    description = "Budget- and accessibility-aware grounded lodging research."
    output_model = AccommodationOutput
    prompt_name = "accommodation_research_v1.txt"

    def __init__(self, pricing_provider: PricingProvider | None = None) -> None:
        """Configure the replaceable nightly pricing provider."""
        self.pricing_provider = pricing_provider or HeuristicPricingProvider()

    async def run(self, context: AgentContext) -> AgentResult:
        """Return ranked, grounded accommodation estimates."""
        requirements = _requirements_from(context)
        try:
            output: AccommodationOutput = await self.generate(context, requirements)
        except Exception:
            output = AccommodationOutput()
        nights = (
            (requirements.end_date - requirements.start_date).days
            if requirements.start_date and requirements.end_date
            else 1
        )
        travelers = requirements.traveler_count or 1
        cap = (
            requirements.budget / Decimal(max(nights, 1)) / Decimal(travelers)
            if requirements.budget
            else None
        )
        grounded = await GroundingService().ground(
            context, [item.model_dump(mode="json") for item in output.options]
        )
        options: list[AccommodationOption] = []
        for item in grounded:
            low, high = self.pricing_provider.estimate(item.get("price_level"))
            options.append(
                AccommodationOption.model_validate(
                    {
                        **item,
                        "estimated_cost_min": low,
                        "estimated_cost_max": high,
                        "price_per_night_cap": cap,
                        "is_estimate": True,
                        "source": "heuristic",
                    }
                )
            )
        return self.envelope(
            output.model_copy(update={"options": options, "per_night_cap": cap}),
            sources=["places", "heuristic-pricing"],
            confidence=sum(int(item.verified) for item in options) / len(options)
            if options
            else 0,
        )


class WeatherAgent(ResearchAgent[WeatherOutput]):
    """Fetch deterministic forecasts and flag risks and outdoor suitability."""

    name = "weather"
    description = "Date-aware forecast, risk flags, packing, and outdoor suitability."
    output_model = WeatherOutput
    prompt_name = "weather_research_v1.txt"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return forecast-backed weather guidance or climatology estimates."""
        requirements = _requirements_from(context)
        destination = requirements.destination or ""
        geo = await self.call_tool(context, "geocode", {"address": destination})
        try:
            output: WeatherOutput = await self.generate(context, requirements)
        except Exception:
            output = WeatherOutput(destination=destination)
        forecast: ForecastOutput | None = None
        if (
            isinstance(geo, GeocodeOutput)
            and requirements.start_date
            and requirements.end_date
        ):
            result = await self.call_tool(
                context,
                "get_forecast",
                {
                    "lat": geo.lat,
                    "lng": geo.lng,
                    "start_date": requirements.start_date,
                    "end_date": requirements.end_date,
                },
            )
            forecast = result if isinstance(result, ForecastOutput) else None
        expected_dates = _date_range(requirements.start_date, requirements.end_date)
        forecast_by_date = {
            item.date: item for item in (forecast.daily if forecast else [])
        }
        days = [
            (
                WeatherDay(
                    date=item.date,
                    summary=f"{item.temp_min_c:.0f}-{item.temp_max_c:.0f}°C",
                    temperature_min=item.temp_min_c,
                    temperature_max=item.temp_max_c,
                    precipitation_probability=item.precipitation_probability,
                    outdoor_suitability=max(
                        0.0, 1.0 - item.precipitation_probability / 100
                    ),
                    is_estimate=False,
                )
                if (item := forecast_by_date.get(day)) is not None
                else WeatherDay(
                    date=day,
                    summary="Climatological note; forecast range exceeded.",
                    outdoor_suitability=0.5,
                    is_estimate=True,
                )
            )
            for day in expected_dates
        ]
        if days and forecast is not None:
            output = output.model_copy(
                update={
                    "destination": destination,
                    "days": days,
                    "risk_flags": [risk.message for risk in forecast.risks],
                }
            )
            return self.envelope(
                output,
                sources=(
                    ["geocode", forecast.provider]
                    + (
                        ["llm-climatology"]
                        if any(day.is_estimate for day in days)
                        else []
                    )
                ),
                confidence=0.9 if not any(day.is_estimate for day in days) else 0.6,
            )
        output = output.model_copy(
            update={
                "destination": destination,
                "days": [
                    WeatherDay(
                        date=day,
                        summary="Climatological note; forecast unavailable.",
                        is_estimate=True,
                    )
                    for day in _date_range(
                        requirements.start_date, requirements.end_date
                    )
                ],
            }
        )
        return self.envelope(output, sources=["llm-climatology"], confidence=0.3)


class ActivityAgent(ResearchAgent[ActivityOutput]):
    """Discover and ground interest-matched activities and meals."""

    name = "activity"
    description = "Grounded activities, meals, and weather-sensitive suggestions."
    output_model = ActivityOutput
    prompt_name = "activity_research_v1.txt"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return grounded interest-matched activities and meals."""
        requirements = _requirements_from(context)
        try:
            output: ActivityOutput = await self.generate(context, requirements)
        except Exception:
            output = ActivityOutput()
        grounded = await GroundingService().ground(
            context,
            [item.model_dump(mode="json") for item in output.activities],
            name_key="title",
        )
        activities = [ActivityOption.model_validate(item) for item in grounded]
        return self.envelope(
            output.model_copy(update={"activities": activities}),
            sources=["places", "llm"],
            confidence=sum(int(item.get("verified", False)) for item in grounded)
            / len(grounded)
            if grounded
            else 0,
        )


class LocalIntelligenceAgent(ResearchAgent[LocalIntelligenceOutput]):
    """Provide explicitly unverified general local guidance."""

    name = "local_intelligence"
    description = "Cultural, safety, payment, language, and local transport guidance."
    output_model = LocalIntelligenceOutput
    prompt_name = "local_intelligence_research_v1.txt"

    async def run(self, context: AgentContext) -> AgentResult:
        """Return explicitly unverified local guidance."""
        requirements = _requirements_from(context)
        try:
            output: LocalIntelligenceOutput = await self.generate(context, requirements)
        except Exception as error:
            output = LocalIntelligenceOutput(
                destination=requirements.destination or "",
                verification_note="LLM unavailable; no local guidance was generated.",
            )
            return self.envelope(
                output, errors=[f"LLM degraded: {type(error).__name__}"]
            )
        output = output.model_copy(
            update={
                "verified": False,
                "verification_note": (
                    "General LLM guidance; verify with official local sources."
                ),
                "sources": sorted(set(output.sources + ["llm-general-guidance"])),
            }
        )
        return self.envelope(output, sources=output.sources, confidence=0.35)


def _date_range(start: date | None, end: date | None) -> list[date]:
    """Return a bounded inclusive date range for fallback weather rows."""
    if start is None or end is None:
        return []
    return [
        date.fromordinal(value)
        for value in range(start.toordinal(), end.toordinal() + 1)
    ]
