"""Configurable, explicitly labeled transport fare estimation tool."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.tools.base import Tool
from app.tools.schemas import FareEstimateInput, FareEstimateOutput, FareMode


class FareModeConfig(BaseModel):
    """Configurable per-mode estimation rates and duration assumptions."""

    minimum_fare: float = Field(ge=0)
    base_fare: float = Field(ge=0)
    min_per_km: float = Field(ge=0)
    max_per_km: float = Field(ge=0)
    speed_kmh: float = Field(gt=0)
    duration_buffer_s: int = Field(default=0, ge=0)


class FareConfig(BaseModel):
    """Configuration file contract for heuristic fare estimates."""

    currency: str = Field(min_length=1, max_length=3)
    source: str = Field(min_length=1)
    modes: dict[FareMode, FareModeConfig]


class FareProvider(ABC):
    """Interface for future verified fare providers."""

    @abstractmethod
    async def estimate(self, request: FareEstimateInput) -> FareEstimateOutput:
        """Return a fare estimate or a provider-supplied fare result."""
        raise NotImplementedError


class HeuristicFareProvider(FareProvider):
    """Calculate fare ranges from an explicit configuration file."""

    def __init__(self, config: FareConfig) -> None:
        """Create a heuristic estimator from validated rate configuration."""
        self.config = config

    @classmethod
    def from_file(cls, path: str | Path) -> HeuristicFareProvider:
        """Load and validate fare rates from a JSON configuration file."""
        config_path = Path(path)
        if not config_path.is_absolute() and not config_path.is_file():
            config_path = Path(__file__).parent / config_path.name
        if not config_path.is_file():
            raise FileNotFoundError(f"Fare estimate configuration not found: {path}")
        return cls(FareConfig.model_validate_json(config_path.read_text("utf-8")))

    async def estimate(self, request: FareEstimateInput) -> FareEstimateOutput:
        """Calculate a non-binding fare range and travel duration."""
        mode = self.config.modes[request.mode]
        distance_km = request.distance_m / 1000
        minimum = max(
            mode.minimum_fare,
            mode.base_fare + distance_km * mode.min_per_km,
        )
        maximum = max(
            minimum,
            mode.base_fare + distance_km * mode.max_per_km,
        )
        duration = round(distance_km / mode.speed_kmh * 3600) + mode.duration_buffer_s
        return FareEstimateOutput(
            origin=request.origin,
            destination=request.destination,
            mode=request.mode,
            estimated_fare_min=round(minimum, 2),
            estimated_fare_max=round(maximum, 2),
            currency=self.config.currency,
            duration_s=duration,
            is_estimate=True,
            source=self.config.source,
        )


class TransportEstimatorTool(Tool[FareEstimateInput, FareEstimateOutput]):
    """Estimate transport fare and duration using configured heuristics."""

    name = "estimate_transport"
    description = (
        "Estimate a transport fare range; the result is not a live quote or booking."
    )
    input_schema = FareEstimateInput
    output_schema = FareEstimateOutput
    provider = "transport_estimator"

    def __init__(self, provider: FareProvider | None = None) -> None:
        """Create an estimator backed by a replaceable fare provider."""
        settings = get_settings()
        self.fare_provider = provider or HeuristicFareProvider.from_file(
            settings.fare_config_path
        )
        super().__init__(cache_ttl_s=0)

    async def _run(self, payload: FareEstimateInput) -> FareEstimateOutput:
        """Delegate a validated fare estimate to the configured provider."""
        result = await self.fare_provider.estimate(payload)
        return result
