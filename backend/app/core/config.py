"""Application settings loaded from environment variables and .env."""

from functools import lru_cache

from pydantic import SecretStr
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
    openai_base_url: str | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    google_maps_api_key: SecretStr | None = None
    weather_api_key: SecretStr | None = None
    jwt_secret: SecretStr = SecretStr("development-only-change-me")
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

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
