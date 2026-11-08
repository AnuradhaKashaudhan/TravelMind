"""Application settings loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    db_url: str = "postgresql+asyncpg://travelmind:travelmind@localhost:5432/travelmind"
    redis_url: str = "redis://localhost:6379/0"
    gemini_api_key: SecretStr | None = None
    llm_provider: str = "gemini"
    fallback_llm_provider: str | None = None
    gemini_model: str = "gemini-2.0-flash"
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    gemini_embedding_model: str = "gemini-embedding-001"
    openai_embedding_model: str = "text-embedding-3-small"
    simple_model: str | None = None
    supervisor_model: str | None = None
    itinerary_model: str | None = None
    llm_timeout_s: float = 45.0
    llm_retries: int = 2
    llm_retry_base_s: float = 0.5
    llm_input_cost_per_million_usd: float = 0.0
    llm_output_cost_per_million_usd: float = 0.0
    embedding_dimensions: int = 768
    use_fake_providers: bool = False
    google_maps_api_key: SecretStr | None = None
    weather_api_key: SecretStr | None = None
    places_cache_ttl_s: int = 86400
    geocoding_cache_ttl_s: int = 2592000
    weather_cache_ttl_s: int = 3600
    routes_cache_ttl_s: int = 21600
    tool_timeout_s: float = 15.0
    tool_retries: int = 2
    provider_rate_limit_per_s: float = 5.0
    provider_rate_limit_burst: int = 10
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_reset_s: float = 30.0
    fare_config_path: str = "app/tools/fare_estimates.json"
    openweather_base_url: str = "https://api.openweathermap.org/data/2.5"
    jwt_secret: SecretStr = SecretStr("development-only-change-me")
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    @field_validator("embedding_dimensions")
    @classmethod
    def validate_embedding_dimensions(cls, value: int) -> int:
        """Ensure embeddings match the database's fixed vector dimensions."""
        if value != 768:
            raise ValueError(
                "embedding_dimensions must be 768 to match pgvector Vector(768) columns"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        """Return configured CORS origins as a normalized list."""
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached settings object."""
    return Settings()
