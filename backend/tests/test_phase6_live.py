"""Opt-in live Phase 6 smoke coverage."""

import os

import pytest

from scripts.run_full_plan_demo import run


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_full_plan_runs_against_real_providers() -> None:
    """Run the Manali plan against configured providers when explicitly enabled."""
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_TESTS=1 to run live provider coverage.")
    await run("Manali, India", fake=False)
