"""Requirement extraction, clarification, and validated task planning."""

from __future__ import annotations

import json
from importlib.resources import files

import structlog

from app.llm.router import LLMRouter
from app.orchestration.agent_base import AgentRegistry
from app.schemas.agent_io import SupervisorPlan, TaskSpec, TripRequirements

logger = structlog.get_logger(__name__)
_REQUIRED_FIELDS = ("destination", "start_date", "end_date", "traveler_count", "budget")


class SupervisorAgent:
    """Translate a user request into requirements and a registered-agent plan."""

    def __init__(self, llm_router: LLMRouter, agents: AgentRegistry) -> None:
        """Bind the LLM router and registered agent catalog."""
        self.llm_router = llm_router
        self.agents = agents

    async def extract_requirements(self, user_request: str) -> TripRequirements:
        """Extract typed trip requirements from natural language."""
        prompt = (
            files("app.llm.prompts")
            .joinpath("supervisor_requirements_v1.txt")
            .read_text(encoding="utf-8")
        )
        requirements = await self.llm_router.generate_structured_for_agent(
            "supervisor",
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_request},
            ],
            TripRequirements,
            max_repairs=2,
        )
        return requirements.model_copy(
            update={"missing_fields": self.missing_required_fields(requirements)}
        )

    def missing_required_fields(self, requirements: TripRequirements) -> list[str]:
        """Return required trip fields that still need user clarification."""
        values = requirements.model_dump()
        return [field for field in _REQUIRED_FIELDS if values.get(field) is None]

    def clarification_questions(self, requirements: TripRequirements) -> list[str]:
        """Build concise questions for currently missing required values."""
        questions: dict[str, str] = {
            "destination": "Where would you like to travel?",
            "start_date": "What date would you like the trip to start?",
            "end_date": "What date should the trip end?",
            "traveler_count": "How many travelers are going?",
            "budget": "What is your total trip budget?",
        }
        return [
            questions[field] for field in self.missing_required_fields(requirements)
        ]

    async def build_plan(self, requirements: TripRequirements) -> SupervisorPlan:
        """Ask the model for a plan and use a validated deterministic fallback."""
        catalog = self.agents.catalog()
        prompt = (
            files("app.llm.prompts")
            .joinpath("supervisor_plan_v1.txt")
            .read_text(encoding="utf-8")
        )
        user_content = json.dumps(
            {
                "requirements": requirements.model_dump(mode="json"),
                "agents": [
                    {"name": name, "description": description}
                    for name, description in catalog
                ],
            },
            separators=(",", ":"),
        )
        try:
            plan = await self.llm_router.generate_structured_for_agent(
                "supervisor",
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_content},
                ],
                SupervisorPlan,
                max_repairs=2,
            )
            known_agents = set(self.agents.names())
            unknown = sorted({task.agent_name for task in plan.tasks} - known_agents)
            if unknown:
                raise ValueError(f"Plan references unregistered agents: {unknown}")
            return self._enforce_domain_dependencies(plan)
        except (ValueError, KeyError) as error:
            logger.warning(
                "supervisor_plan_fallback",
                error_type=type(error).__name__,
            )
            return self.default_plan()

    def default_plan(self) -> SupervisorPlan:
        """Build a dependency-aware plan from registered agent names."""
        names = set(self.agents.names())
        tasks = [
            TaskSpec(task_key=name, agent_name=name) for name in self.agents.names()
        ]
        by_name = {task.agent_name: task for task in tasks}
        accommodation = by_name.get("accommodation")
        if accommodation is not None and "destination" in names:
            accommodation.depends_on = ["destination"]
        activity = by_name.get("activity")
        if activity is not None and "destination" in names:
            activity.depends_on = ["destination"]
        itinerary = by_name.get("itinerary")
        if itinerary is not None:
            itinerary.depends_on = [
                name
                for name in (
                    "destination",
                    "transport",
                    "accommodation",
                    "weather",
                    "activity",
                    "local_intelligence",
                )
                if name in names
            ]
        budget = by_name.get("budget")
        if budget is not None:
            budget.depends_on = [
                name
                for name in ("transport", "accommodation", "activity")
                if name in names
            ]
        validation = by_name.get("validation")
        if validation is not None:
            validation.depends_on = [
                name for name in ("itinerary", "budget") if name in names
            ]
        return SupervisorPlan.model_validate(
            {"tasks": [task.model_dump() for task in tasks], "rationale": "fallback"}
        )

    def _enforce_domain_dependencies(
        self,
        plan: SupervisorPlan,
    ) -> SupervisorPlan:
        """Ensure composed work depends on all registered upstream specialists."""
        dependencies: dict[str, set[str]] = {
            "accommodation": {"destination"},
            "activity": {"destination"},
            "itinerary": {
                "destination",
                "transport",
                "accommodation",
                "weather",
                "activity",
                "local_intelligence",
            },
            "budget": {"transport", "accommodation", "activity"},
            "validation": {"itinerary", "budget"},
        }
        updated_tasks: list[TaskSpec] = []
        for task in plan.tasks:
            required_agents = dependencies.get(task.agent_name, set())
            if task.agent_name == "itinerary":
                required_agents = required_agents | {
                    "local_intelligence",
                }
            required_keys = {
                upstream.task_key
                for upstream in plan.tasks
                if upstream.agent_name in required_agents
                and upstream.task_key != task.task_key
            }
            updated_tasks.append(
                task.model_copy(
                    update={"depends_on": sorted(set(task.depends_on) | required_keys)}
                )
            )
        return SupervisorPlan.model_validate(
            {
                "tasks": [task.model_dump() for task in updated_tasks],
                "rationale": plan.rationale,
            }
        )
