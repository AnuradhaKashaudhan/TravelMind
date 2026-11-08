"""Registry for lookup and interoperable export of typed tools."""

from __future__ import annotations

from typing import Any

from app.core.config import Settings, get_settings
from app.tools.base import Tool
from app.tools.geocoding import GeocodeTool, ReverseGeocodeTool
from app.tools.places import (
    NearbySearchTool,
    PlaceDetailsTool,
    TextSearchTool,
    VerifyPlaceTool,
)
from app.tools.routes import DistanceMatrixTool, RouteTool
from app.tools.transport_estimator import TransportEstimatorTool
from app.tools.weather import WeatherTool


class ToolRegistry:
    """Named collection of typed tools with OpenAI-compatible specifications."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        """Register a tool, rejecting duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"Tool is already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any]:
        """Look up a tool by its public name."""
        try:
            return self._tools[name]
        except KeyError as error:
            raise KeyError(f"Tool is not registered: {name}") from error

    def export_tool_specs(self) -> list[dict[str, Any]]:
        """Export tool definitions in OpenAI/LangGraph function-tool format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema.model_json_schema(),
                },
            }
            for tool in self._tools.values()
        ]

    def export_schemas(self) -> dict[str, dict[str, Any]]:
        """Export both validated input and output JSON schemas."""
        return {
            tool.name: {
                "input": tool.input_schema.model_json_schema(),
                "output": tool.output_schema.model_json_schema(),
            }
            for tool in self._tools.values()
        }

    def as_langchain_tools(self) -> list[Any]:
        """Return wrappers compatible with LangChain tool calling."""
        return [tool.as_langchain_tool() for tool in self._tools.values()]

    def names(self) -> list[str]:
        """Return registered names in registration order."""
        return list(self._tools)


def build_default_tool_registry(
    settings: Settings | None = None,
) -> ToolRegistry:
    """Construct the Phase 3 tool set from application settings."""
    config = settings or get_settings()
    if config.use_fake_providers:
        return _build_fake_registry(config)
    registry = ToolRegistry()
    tools: list[Tool[Any, Any]] = [
        TextSearchTool(),
        NearbySearchTool(),
        PlaceDetailsTool(),
        VerifyPlaceTool(),
        GeocodeTool(),
        ReverseGeocodeTool(),
        RouteTool(),
        DistanceMatrixTool(),
        WeatherTool(),
        TransportEstimatorTool(),
    ]
    for tool in tools:
        registry.register(tool)
    return registry


def _build_fake_registry(settings: Settings) -> ToolRegistry:
    """Return no-network fake tool contracts for local development."""
    from app.tools.fake import build_fake_tool_registry

    return build_fake_tool_registry(settings)
