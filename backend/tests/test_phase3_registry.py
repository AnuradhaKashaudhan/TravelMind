"""Registry compatibility and fake-provider selection tests."""

from __future__ import annotations

from app.core.config import Settings
from app.tools.registry import build_default_tool_registry
from app.tools.schemas import PlaceSearchOutput


async def test_fake_tool_registry_exports_and_invokes_without_network() -> None:
    """Fake registry preserves names, JSON specs, and LangChain callability."""
    registry = build_default_tool_registry(Settings(use_fake_providers=True))
    assert "places_text_search" in registry.names()
    assert "get_forecast" in registry.export_schemas()
    assert registry.export_tool_specs()[0]["type"] == "function"

    result = await registry.get("places_text_search").run({"query": "Manali"})
    assert isinstance(result, PlaceSearchOutput)
    assert result.places == []

    langchain_tools = registry.as_langchain_tools()
    assert len(langchain_tools) == len(registry.names())
    assert langchain_tools[0].name == "places_text_search"
    langchain_result = await langchain_tools[0].ainvoke({"query": "Manali"})
    assert isinstance(langchain_result, PlaceSearchOutput)
