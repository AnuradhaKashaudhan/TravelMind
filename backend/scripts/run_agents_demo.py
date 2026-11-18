"""Run the six Phase 5 agents in fake or configured live mode."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app  # noqa: E402
from app.agents import build_default_agent_registry
from app.core.config import Settings
from app.llm.factory import build_llm_router
from app.llm.fake import FakeLLMClient
from app.llm.router import LLMRouter
from app.orchestration.agent_base import AgentContext
from app.schemas.agent_io import TaskSpec, TripRequirements
from app.tools.fake import build_fake_tool_registry
from app.tools.registry import build_default_tool_registry

if Path("/app").is_dir():
    assert app.__file__ is not None and app.__file__.startswith("/app/app")


async def run(destination: str, fake: bool) -> None:
    """Execute each specialist and print a compact readable summary."""
    settings = Settings(use_fake_providers=fake)
    if fake:
        responses = _fake_responses(destination)
        router: LLMRouter = LLMRouter(
            {"fake": FakeLLMClient(responses)},
            primary_provider="fake",
            settings=settings,
        )
        tools = build_fake_tool_registry(settings)
    else:
        router = build_llm_router(settings)
        tools = build_default_tool_registry(settings)
    registry = build_default_agent_registry(settings, router, tools)
    state = {
        "requirements": TripRequirements(
            destination=destination,
            origin="Delhi, India",
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=9),
            traveler_count=2,
            budget=1000,
            interests=["hiking", "culture"],
            food=["local vegetarian food"],
        ),
    }
    try:
        for name in registry.names():
            started = time.perf_counter()
            result = await registry.get(name).run(
                AgentContext(
                    state=state,
                    task=TaskSpec(task_key=name, agent_name=name),
                    upstream_results={},
                    llm_router=router,
                    tool_registry=tools,
                    logger=structlog.get_logger("demo"),
                )
            )
            elapsed = round((time.perf_counter() - started) * 1000)
            data = result.data or {}
            lists = [value for value in data.values() if isinstance(value, list)]
            records = [value for value in lists if value and isinstance(value[0], dict)]
            unverified = sum(
                sum(not bool(item.get("verified")) for item in group)
                for group in records
            )
            verified = sum(
                bool(item.get("verified")) for group in records for item in group
            )
            print(
                f"{name}: records={sum(map(len, lists))} "
                f"verified={verified} unverified={unverified} "
                f"confidence={result.confidence or 0:.2f} "
                f"duration_ms={result.duration_ms or elapsed}"
            )
            for group in records:
                for item in group:
                    if not item.get("verified", True):
                        label = (
                            item.get("name")
                            or item.get("title")
                            or item.get("mode")
                            or "unnamed"
                        )
                        note = item.get("verification_note") or "unverified"
                        print(f"  unverified: {label} - {note}")
    finally:
        await tools.aclose()
        await router.aclose()


def main() -> None:
    """Parse demo options and run the async workflow."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default="Manali, India")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.destination, fake=not args.live))


def _fake_responses(destination: str) -> list[str]:
    """Return realistic deterministic Manali-style responses for fake mode."""
    return [
        json.dumps(
            {
                "destination": destination,
                "overview": (
                    "A Himalayan valley with mountain scenery and local culture."
                ),
                "popular_attractions": [
                    {
                        "name": "Hadimba Temple",
                        "description": "Historic cedar temple.",
                    },
                    {
                        "name": "Solang Valley",
                        "description": "Mountain adventure area.",
                    },
                ],
                "hidden_gems": [
                    {
                        "name": "Hallucinated Sky Bridge",
                        "description": "Unverified suggestion.",
                    }
                ],
                "best_areas_to_stay": ["Old Manali", "Vashisht"],
            }
        ),
        json.dumps({"recommended_mode": "bus"}),
        json.dumps(
            {
                "options": [
                    {
                        "name": "Snow Valley Resorts",
                        "description": "Accessible valley lodging.",
                    },
                    {
                        "name": "Hallucinated Grand Hotel",
                        "description": "Unverified lodging suggestion.",
                    },
                ]
            }
        ),
        json.dumps(
            {
                "destination": destination,
                "packing_suggestions": ["Warm layers", "Rain shell"],
            }
        ),
        json.dumps(
            {
                "activities": [
                    {
                        "title": "Mall Road",
                        "description": "Local shopping and food.",
                        "category": "meal",
                        "weather_sensitive": False,
                    },
                    {
                        "title": "Hallucinated Adventure Park",
                        "description": "Unverified activity suggestion.",
                        "category": "activity",
                        "weather_sensitive": True,
                    },
                ]
            }
        ),
        json.dumps(
            {
                "destination": destination,
                "cultural_etiquette": ["Dress respectfully at temples."],
                "safety_notes": ["Check road conditions before mountain travel."],
                "transport_notes": ["Shared taxis are common locally."],
                "payment_currency": ["Indian rupee (INR)"],
                "language_basics": ["Namaste means hello."],
            }
        ),
    ]


if __name__ == "__main__":
    main()
