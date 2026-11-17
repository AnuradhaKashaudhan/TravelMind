"""Shared execution helpers for grounded research agents."""

from __future__ import annotations

import json
from abc import abstractmethod
from importlib.resources import files
from typing import Any, Generic, TypeVar, cast

from pydantic import BaseModel

from app.models.enums import AgentRunStatus
from app.orchestration.agent_base import AgentContext, BaseAgent
from app.schemas.agent_io import AgentResult, TripRequirements

OutputT = TypeVar("OutputT", bound=BaseModel)


class ResearchAgent(BaseAgent, Generic[OutputT]):
    """Base class for typed, bounded research agents."""

    output_model: type[OutputT]
    prompt_name: str
    role: str = "simple"

    def requirements(self, context: AgentContext) -> TripRequirements:
        """Read typed trip requirements from graph state."""
        value = context.state.get("requirements")
        if isinstance(value, TripRequirements):
            return value
        if isinstance(value, dict):
            return TripRequirements.model_validate(value)
        return TripRequirements.model_validate(context.task.inputs)

    async def prompt(
        self, context: AgentContext, requirements: TripRequirements
    ) -> list[dict[str, str]]:
        """Build a versioned prompt with requirements and upstream evidence."""
        template = (
            files("app.llm.prompts")
            .joinpath(self.prompt_name)
            .read_text(encoding="utf-8")
        )
        payload = {
            "requirements": requirements.model_dump(mode="json"),
            "upstream_results": {
                key: value.model_dump(mode="json")
                for key, value in context.upstream_results.items()
            },
            "degraded_input_note": context.degraded_input_note,
        }
        return [
            {"role": "system", "content": template},
            {"role": "user", "content": json.dumps(payload, separators=(",", ":"))},
        ]

    async def generate(
        self, context: AgentContext, requirements: TripRequirements
    ) -> OutputT:
        """Generate the typed curation portion through the configured router."""
        return await context.llm_router.generate_structured_for_agent(
            self.name,
            await self.prompt(context, requirements),
            self.output_model,
            max_repairs=2,
        )

    async def call_tool(
        self, context: AgentContext, name: str, payload: dict[str, Any]
    ) -> object | None:
        """Run an optional tool and degrade explicitly when it is unavailable."""
        try:
            return cast(object, await context.tool_registry.get(name).run(payload))
        except Exception as error:
            context.logger.warning(
                "research_tool_degraded",
                tool=name,
                error_type=type(error).__name__,
            )
            return None

    def envelope(
        self,
        data: OutputT,
        *,
        sources: list[str] | None = None,
        confidence: float = 0.0,
        errors: list[str] | None = None,
    ) -> AgentResult:
        """Wrap typed output in the standard result envelope."""
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data=data.model_dump(mode="json"),
            sources=sorted(set(sources or [])),
            confidence=max(0.0, min(1.0, confidence)),
            errors=errors or [],
        )

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentResult:
        """Execute one research agent."""
        raise NotImplementedError
