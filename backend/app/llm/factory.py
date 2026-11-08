"""Construct configured LLM providers and the default router."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.llm.base import LLMClient
from app.llm.fake import FakeLLMClient
from app.llm.providers.gemini import GeminiClient
from app.llm.providers.openai_compatible import OpenAICompatibleClient
from app.llm.router import LLMRouter


def build_llm_router(settings: Settings | None = None) -> LLMRouter:
    """Build the configured provider set and primary/fallback router."""
    config = settings or get_settings()
    if config.use_fake_providers:
        fake = FakeLLMClient(dimensions=config.embedding_dimensions)
        return LLMRouter(
            {"fake": fake},
            primary_provider="fake",
            settings=config,
        )

    requested = [config.llm_provider]
    if config.fallback_llm_provider:
        requested.append(config.fallback_llm_provider)
    clients: dict[str, LLMClient] = {}
    for provider in dict.fromkeys(requested):
        if provider == "gemini":
            if config.gemini_api_key is None:
                raise ValueError("GEMINI_API_KEY is required for the Gemini provider")
            clients[provider] = GeminiClient(
                config.gemini_api_key,
                model=config.gemini_model,
                embedding_model=config.gemini_embedding_model,
                dimensions=config.embedding_dimensions,
            )
        elif provider == "openai_compatible":
            if config.openai_api_key is None and config.openai_base_url is None:
                raise ValueError(
                    "Configure OPENAI_API_KEY or OPENAI_BASE_URL for the "
                    "OpenAI-compatible provider"
                )
            clients[provider] = OpenAICompatibleClient(
                base_url=config.openai_base_url,
                api_key=config.openai_api_key,
                model=config.openai_model,
                embedding_model=config.openai_embedding_model,
                dimensions=config.embedding_dimensions,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")
    return LLMRouter(
        clients,
        primary_provider=config.llm_provider,
        fallback_provider=config.fallback_llm_provider,
        settings=config,
    )
