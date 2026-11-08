"""Shared authenticated HTTP client for Google Maps Platform endpoints."""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError


class GoogleMapsClient:
    """Make Google Maps requests without exposing credentials in logs."""

    def __init__(
        self,
        api_key: SecretStr | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Create a client for Places, Geocoding, and Routes endpoints."""
        secret = api_key or get_settings().google_maps_api_key
        self._api_key = secret.get_secret_value() if secret else None
        self._client = http_client or httpx.AsyncClient()
        self._owns_client = http_client is None

    def headers(self) -> dict[str, str]:
        """Return the Google API key header or fail clearly when unconfigured."""
        if self._api_key is None:
            raise ExternalServiceError("Google Maps API key is not configured")
        return {"X-Goog-Api-Key": self._api_key}

    def authenticated_params(
        self, params: dict[str, str | float]
    ) -> dict[str, str | float]:
        """Add the key required by Google Geocoding API without logging it."""
        if self._api_key is None:
            raise ExternalServiceError("Google Maps API key is not configured")
        return {**params, "key": self._api_key}

    @property
    def http(self) -> httpx.AsyncClient:
        """Expose the underlying client for endpoint-specific request methods."""
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._owns_client:
            await self._client.aclose()
