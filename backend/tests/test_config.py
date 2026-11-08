"""Tests for environment-backed application settings."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_coerce_numeric_and_boolean_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """String environment values are coerced to their declared scalar types."""
    environment = {
        "EMBEDDING_DIMENSIONS": "768",
        "LLM_TIMEOUT_S": "60.5",
        "LLM_RETRIES": "3",
        "LLM_RETRY_BASE_S": "0.75",
        "LLM_INPUT_COST_PER_MILLION_USD": "1.25",
        "LLM_OUTPUT_COST_PER_MILLION_USD": "2.5",
        "USE_FAKE_PROVIDERS": "true",
        "PLACES_CACHE_TTL_S": "120",
        "GEOCODING_CACHE_TTL_S": "240",
        "WEATHER_CACHE_TTL_S": "60",
        "ROUTES_CACHE_TTL_S": "180",
        "TOOL_TIMEOUT_S": "8.5",
        "TOOL_RETRIES": "4",
        "PROVIDER_RATE_LIMIT_PER_S": "2.5",
        "PROVIDER_RATE_LIMIT_BURST": "6",
        "CIRCUIT_BREAKER_FAILURE_THRESHOLD": "7",
        "CIRCUIT_BREAKER_RESET_S": "45.5",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)

    assert settings.embedding_dimensions == 768
    assert isinstance(settings.embedding_dimensions, int)
    assert settings.llm_timeout_s == 60.5
    assert settings.llm_retries == 3
    assert settings.llm_retry_base_s == 0.75
    assert settings.llm_input_cost_per_million_usd == 1.25
    assert settings.llm_output_cost_per_million_usd == 2.5
    assert settings.use_fake_providers is True
    assert settings.places_cache_ttl_s == 120
    assert settings.geocoding_cache_ttl_s == 240
    assert settings.weather_cache_ttl_s == 60
    assert settings.routes_cache_ttl_s == 180
    assert settings.tool_timeout_s == 8.5
    assert settings.tool_retries == 4
    assert settings.provider_rate_limit_per_s == 2.5
    assert settings.provider_rate_limit_burst == 6
    assert settings.circuit_breaker_failure_threshold == 7
    assert settings.circuit_breaker_reset_s == 45.5


def test_settings_reject_non_768_embedding_dimensions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedding dimensions cannot differ from the database vector columns."""
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "512")

    with pytest.raises(ValidationError, match="must be 768"):
        Settings(_env_file=None)
