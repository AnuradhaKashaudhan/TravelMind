"""Open-Meteo forecast tool with optional OpenWeatherMap fallback."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime

import httpx
import structlog
from pydantic import BaseModel, Field, SecretStr

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError
from app.tools.base import Tool
from app.tools.schemas import (
    DailyForecast,
    ForecastInput,
    ForecastOutput,
    WeatherRisk,
)

logger = structlog.get_logger(__name__)


class _OpenMeteoDaily(BaseModel):
    """Open-Meteo daily arrays."""

    time: list[date]
    temperature_2m_max: list[float] = Field(alias="temperature_2m_max")
    temperature_2m_min: list[float] = Field(alias="temperature_2m_min")
    precipitation_probability_max: list[float | None] = Field(
        alias="precipitation_probability_max"
    )
    precipitation_sum: list[float] = Field(alias="precipitation_sum")
    wind_speed_10m_max: list[float] = Field(alias="wind_speed_10m_max")
    weather_code: list[int] = Field(alias="weather_code")


class _OpenMeteoResponse(BaseModel):
    """Open-Meteo forecast response."""

    daily: _OpenMeteoDaily


class _OpenWeatherMain(BaseModel):
    """OpenWeatherMap temperatures for a forecast interval."""

    temp_min: float
    temp_max: float


class _OpenWeatherCondition(BaseModel):
    """OpenWeatherMap condition code."""

    id: int


class _OpenWeatherWind(BaseModel):
    """OpenWeatherMap wind speed in meters per second."""

    speed: float


class _OpenWeatherRain(BaseModel):
    """OpenWeatherMap three-hour rainfall."""

    three_hour_mm: float = Field(default=0, alias="3h")


class _OpenWeatherEntry(BaseModel):
    """One three-hour OpenWeatherMap forecast record."""

    dt: int
    main: _OpenWeatherMain
    weather: list[_OpenWeatherCondition] = Field(default_factory=list)
    wind: _OpenWeatherWind = Field(default_factory=lambda: _OpenWeatherWind(speed=0))
    pop: float = 0.0
    rain: _OpenWeatherRain = Field(default_factory=_OpenWeatherRain)


class _OpenWeatherResponse(BaseModel):
    """OpenWeatherMap five-day forecast response."""

    entries: list[_OpenWeatherEntry] = Field(default_factory=list, alias="list")


class WeatherTool(Tool[ForecastInput, ForecastOutput]):
    """Get normalized daily forecasts and derive weather risk flags."""

    name = "get_forecast"
    description = "Get a daily forecast and weather risk flags for travel dates."
    input_schema = ForecastInput
    output_schema = ForecastOutput
    provider = "open_meteo"

    def __init__(
        self,
        *,
        open_meteo_client: httpx.AsyncClient | None = None,
        openweather_client: httpx.AsyncClient | None = None,
        openweather_api_key: SecretStr | None = None,
    ) -> None:
        """Create a weather tool with Open-Meteo as primary provider."""
        self._meteo = open_meteo_client or httpx.AsyncClient()
        self._meteo_owned = open_meteo_client is None
        self._openweather = openweather_client or httpx.AsyncClient()
        self._openweather_owned = openweather_client is None
        key = openweather_api_key or get_settings().weather_api_key
        self._openweather_key = key.get_secret_value() if key else None
        self._openweather_base = get_settings().openweather_base_url.rstrip("/")
        super().__init__(cache_ttl_s=get_settings().weather_cache_ttl_s)

    async def _run(self, payload: ForecastInput) -> ForecastOutput:
        """Fetch Open-Meteo data, then use OpenWeatherMap if configured."""
        try:
            return await self._open_meteo_forecast(payload)
        except (httpx.HTTPError, ValueError) as error:
            if self._openweather_key is None:
                raise ExternalServiceError(
                    "Open-Meteo forecast is unavailable and no fallback is configured"
                ) from error
            logger.warning(
                "weather_primary_failed",
                provider="open_meteo",
                error_type=type(error).__name__,
            )
            return await self._openweather_forecast(payload)

    async def _open_meteo_forecast(self, payload: ForecastInput) -> ForecastOutput:
        """Fetch and normalize Open-Meteo daily forecast arrays."""
        response = await self._meteo.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": payload.lat,
                "longitude": payload.lng,
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,precipitation_sum,"
                    "wind_speed_10m_max,weather_code"
                ),
                "timezone": "auto",
                "start_date": payload.start_date.isoformat(),
                "end_date": payload.end_date.isoformat(),
            },
        )
        response.raise_for_status()
        result = _OpenMeteoResponse.model_validate(response.json())
        daily = result.daily
        lengths = {
            len(daily.time),
            len(daily.temperature_2m_max),
            len(daily.temperature_2m_min),
            len(daily.precipitation_probability_max),
            len(daily.precipitation_sum),
            len(daily.wind_speed_10m_max),
            len(daily.weather_code),
        }
        if len(lengths) != 1:
            raise ValueError("Open-Meteo returned mismatched daily arrays")
        forecasts = [
            DailyForecast(
                date=day,
                temp_min_c=daily.temperature_2m_min[index],
                temp_max_c=daily.temperature_2m_max[index],
                precipitation_probability=daily.precipitation_probability_max[index]
                or 0,
                precipitation_mm=daily.precipitation_sum[index],
                wind_speed_kmh=daily.wind_speed_10m_max[index],
                condition_code=daily.weather_code[index],
            )
            for index, day in enumerate(daily.time)
            if payload.start_date <= day <= payload.end_date
        ]
        if not forecasts:
            raise ValueError(
                "Open-Meteo returned no forecast days in the requested range"
            )
        return _forecast_output("open_meteo", forecasts)

    async def _openweather_forecast(self, payload: ForecastInput) -> ForecastOutput:
        """Fetch and aggregate OpenWeatherMap three-hour forecast records."""
        response = await self._openweather.get(
            f"{self._openweather_base}/forecast",
            params={
                "lat": payload.lat,
                "lon": payload.lng,
                "appid": self._openweather_key,
                "units": "metric",
            },
        )
        response.raise_for_status()
        result = _OpenWeatherResponse.model_validate(response.json())
        grouped: dict[date, list[_OpenWeatherEntry]] = defaultdict(list)
        for item in result.entries:
            day = datetime.fromtimestamp(item.dt, tz=UTC).date()
            if payload.start_date <= day <= payload.end_date:
                grouped[day].append(item)
        forecasts: list[DailyForecast] = []
        for day, entries in sorted(grouped.items()):
            weather = entries[0].weather[0] if entries[0].weather else None
            forecasts.append(
                DailyForecast(
                    date=day,
                    temp_min_c=min(item.main.temp_min for item in entries),
                    temp_max_c=max(item.main.temp_max for item in entries),
                    precipitation_probability=max(item.pop for item in entries) * 100,
                    precipitation_mm=max(
                        item.rain.three_hour_mm for item in entries
                    ),
                    wind_speed_kmh=max(item.wind.speed * 3.6 for item in entries),
                    condition_code=weather.id if weather else 0,
                )
            )
        if not forecasts:
            raise ExternalServiceError(
                "OpenWeatherMap has no forecast data for the requested dates"
            )
        return _forecast_output("openweathermap", forecasts)

    async def aclose(self) -> None:
        """Close HTTP clients owned by this tool."""
        if self._meteo_owned:
            await self._meteo.aclose()
        if self._openweather_owned:
            await self._openweather.aclose()


def flag_weather_risks(
    daily: list[DailyForecast],
) -> list[WeatherRisk]:
    """Flag heavy rain, extreme temperatures, and storm codes by severity."""
    risks: list[WeatherRisk] = []
    for forecast in daily:
        if forecast.precipitation_probability >= 60 or forecast.precipitation_mm >= 20:
            severity = (
                "high"
                if forecast.precipitation_probability >= 80
                or forecast.precipitation_mm >= 40
                else "moderate"
            )
            risks.append(
                WeatherRisk(
                    kind="heavy_rain",
                    severity=severity,
                    date=forecast.date,
                    message="Heavy rain may disrupt outdoor activities or travel.",
                )
            )
        if forecast.temp_max_c >= 35:
            risks.append(
                WeatherRisk(
                    kind="extreme_heat",
                    severity="high" if forecast.temp_max_c >= 40 else "moderate",
                    date=forecast.date,
                    message="High temperatures may create heat-related travel risk.",
                )
            )
        if forecast.temp_min_c <= -10:
            risks.append(
                WeatherRisk(
                    kind="extreme_cold",
                    severity="high" if forecast.temp_min_c <= -20 else "moderate",
                    date=forecast.date,
                    message="Very low temperatures may affect traveler safety.",
                )
            )
        is_storm = forecast.condition_code in {95, 96, 99} or (
            200 <= forecast.condition_code < 300
        )
        if is_storm:
            risks.append(
                WeatherRisk(
                    kind="storm",
                    severity=(
                        "high"
                        if forecast.condition_code in {96, 99}
                        or 200 <= forecast.condition_code < 300
                        else "moderate"
                    ),
                    date=forecast.date,
                    message="Thunderstorm conditions are forecast.",
                )
            )
    return risks


def _forecast_output(provider: str, daily: list[DailyForecast]) -> ForecastOutput:
    """Construct a forecast response with derived weather risks."""
    return ForecastOutput(
        provider=provider,
        daily=daily,
        risks=flag_weather_risks(daily),
    )


class WeatherTools:
    """Facade exposing ``get_forecast`` as a method."""

    def __init__(self, tool: WeatherTool | None = None) -> None:
        """Create the facade with an optional injected weather tool."""
        self.tool = tool or WeatherTool()

    async def get_forecast(
        self,
        lat: float,
        lng: float,
        start_date: date,
        end_date: date,
    ) -> ForecastOutput:
        """Get a normalized date-bounded forecast."""
        return await self.tool.run(
            {
                "lat": lat,
                "lng": lng,
                "start_date": start_date,
                "end_date": end_date,
            }
        )
