"""LangGraph workflow for requirement extraction and task execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.agents.budget import BudgetOptimizer
from app.agents.itinerary import regenerate_days
from app.agents.validation import validate_itinerary
from app.models.enums import AgentRunStatus, ItineraryItemType
from app.orchestration.events import EventType, WorkflowEvent
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.repair import remove_duplicate_places, validate_and_repair
from app.orchestration.state import PlanningState
from app.orchestration.supervisor import SupervisorAgent
from app.schemas.agent_io import (
    ActivityOutput,
    AgentResult,
    BudgetReport,
    ItineraryItemPlan,
    ItineraryPlan,
    ValidationIssue,
    ValidationReport,
    WeatherOutput,
)


def build_planning_graph(
    supervisor: SupervisorAgent,
    executor: WorkflowExecutor,
    checkpointer: object,
) -> CompiledStateGraph[PlanningState, None, PlanningState, PlanningState]:
    """Compile the planning workflow with clarification interrupts and checkpoints."""

    async def start_run(state: PlanningState) -> dict[str, object]:
        event = WorkflowEvent(
            event_type=EventType.RUN_STARTED,
            trip_id=state["trip_id"],
            run_id=state["run_id"],
        )
        await executor.event_bus.publish(event)
        return {"event_log": [event.model_dump(mode="json")]}

    async def parse_requirements(state: PlanningState) -> dict[str, object]:
        requirements = await supervisor.extract_requirements(state["user_request"])
        return {"requirements": requirements}

    async def clarify(state: PlanningState) -> dict[str, object]:
        requirements = state.get("requirements")
        if requirements is None:
            raise ValueError("Requirements must be parsed before clarification")
        questions = supervisor.clarification_questions(requirements)
        event = WorkflowEvent(
            event_type=EventType.CLARIFICATION_NEEDED,
            trip_id=state["trip_id"],
            run_id=state["run_id"],
            payload={"question_count": len(questions)},
        )
        await executor.event_bus.publish(event)
        return {
            "clarification_questions": questions,
            "event_log": [event.model_dump(mode="json")],
        }

    async def await_clarification(state: PlanningState) -> dict[str, object]:
        requirements = state.get("requirements")
        if requirements is None:
            raise ValueError("Requirements must be parsed before clarification")
        answers = interrupt(
            {
                "questions": state.get("clarification_questions", []),
                "fields": requirements.missing_fields,
            }
        )
        if not isinstance(answers, dict):
            raise ValueError("Clarification answers must be an object")
        updated = requirements.model_dump(mode="json")
        updated.update(answers)
        requirements = type(requirements).model_validate(updated)
        requirements = requirements.model_copy(
            update={"missing_fields": supervisor.missing_required_fields(requirements)}
        )
        return {
            "requirements": requirements,
            "clarification_questions": supervisor.clarification_questions(requirements),
        }

    async def plan(state: PlanningState) -> dict[str, object]:
        requirements = state.get("requirements")
        if requirements is None:
            raise ValueError("Requirements must be available before planning")
        return {"plan": await supervisor.build_plan(requirements)}

    async def execute(state: PlanningState) -> dict[str, object]:
        result = await executor.execute(state)
        previous_events = len(state.get("event_log", []))
        return {
            "task_states": result["task_states"],
            "results": result["results"],
            "approval_requests": result.get("approval_requests", {}),
            "errors": result.get("errors", []),
            "event_log": result.get("event_log", [])[previous_events:],
        }

    async def finalize(state: PlanningState) -> dict[str, object]:
        failed = any(
            task_state.get("status") in {AgentRunStatus.FAILED, AgentRunStatus.SKIPPED}
            for task_state in state.get("task_states", {}).values()
        )
        event = WorkflowEvent(
            event_type=EventType.RUN_FAILED if failed else EventType.RUN_COMPLETED,
            trip_id=state["trip_id"],
            run_id=state["run_id"],
            payload={"failed": failed},
        )
        await executor.event_bus.publish(event)
        return {
            "errors": state.get("errors", []),
            "event_log": [event.model_dump(mode="json")],
        }

    async def validate_and_repair_node(
        state: PlanningState,
    ) -> dict[str, object]:
        """Run the bounded deterministic repair loop after agent validation."""
        results = dict(state.get("results", {}))
        validation_result = results.get("validation")
        itinerary_result = results.get("itinerary")
        if validation_result is None or itinerary_result is None:
            return {}
        validation = ValidationReport.model_validate(validation_result.data or {})
        itinerary = ItineraryPlan.model_validate(itinerary_result.data or {})
        requirements = state.get("requirements")
        if requirements is None:
            return {}
        activity_result = results.get("activity")
        activity_output = (
            ActivityOutput.model_validate(activity_result.data)
            if activity_result and activity_result.data
            else ActivityOutput()
        )
        candidates = [
            ItineraryItemPlan(
                item_type=(
                    ItineraryItemType.MEAL
                    if activity.category == "meal"
                    else ItineraryItemType.ACTIVITY
                ),
                title=activity.title,
                description=activity.description,
                place_id=activity.place_id,
                latitude=activity.latitude,
                longitude=activity.longitude,
                estimated_cost=activity.estimated_cost,
                currency=activity.currency,
                source_agent="activity",
                verified=activity.verified,
                tentative=not activity.verified,
                is_estimate=activity.is_estimate,
                weather_sensitive=activity.weather_sensitive,
                opening_hours=activity.opening_hours,
                metadata={
                    "duration_minutes": activity.duration_minutes or 90,
                    "verification_status": activity.verification_status,
                },
            )
            for activity in activity_output.activities
        ]
        weather_result = results.get("weather")
        weather = (
            WeatherOutput.model_validate(weather_result.data)
            if weather_result and weather_result.data
            else None
        )
        repaired = itinerary
        repaired_budget = _budget_from_results(results)

        async def repair(days: set[int]) -> None:
            nonlocal repaired
            repaired = regenerate_days(
                repaired,
                days,
                requirements,
                candidates,
                weather,
                include_unverified=True,
            )

        async def repair_with_issues(
            days: set[int],
            issues: Sequence[ValidationIssue],
        ) -> None:
            nonlocal repaired, repaired_budget
            issue_types = {getattr(issue, "type", None) for issue in issues}
            target_days = days or {day.day_number for day in repaired.days}
            if any(
                getattr(issue, "type", None) == "consistency"
                and "duplicated" in getattr(issue, "message", "").lower()
                for issue in issues
            ):
                repaired = remove_duplicate_places(repaired, target_days)
            if "schedule" in issue_types or "timing" in issue_types:
                repaired = regenerate_days(
                    repaired,
                    target_days,
                    requirements,
                    candidates,
                    weather,
                    include_unverified=True,
                )
            if "budget" in issue_types:
                repaired_budget = BudgetOptimizer().build_report(
                    requirements,
                    results,
                    what_if="reduce by 15%",
                )
                repaired = regenerate_days(
                    repaired,
                    target_days,
                    requirements,
                    candidates,
                    weather,
                    include_unverified=True,
                )

        async def revalidate() -> ValidationReport:
            return validate_itinerary(
                repaired,
                requirements,
                repaired_budget,
                weather,
            )

        loop = await validate_and_repair(
            trip_id=state["trip_id"],
            run_id=state["run_id"],
            report=validation,
            validate=revalidate,
            repair=repair,
            repair_with_issues=repair_with_issues,
            event_bus=executor.event_bus,
        )
        updated_itinerary = itinerary_result.model_copy(
            update={"data": repaired.model_dump(mode="json")}
        )
        updated_validation = validation_result.model_copy(
            update={"data": loop.report.model_dump(mode="json")}
        )
        return {
            "results": {
                "itinerary": updated_itinerary,
                "validation": updated_validation,
                **(
                    {
                        "budget": results["budget"].model_copy(
                            update={"data": repaired_budget.model_dump(mode="json")}
                        )
                    }
                    if repaired_budget is not None and "budget" in results
                    else {}
                ),
            },
            "itinerary_plan": repaired,
            "validation_report": loop.report,
        }

    def after_requirements(state: PlanningState) -> str:
        requirements = state.get("requirements")
        if requirements is None:
            raise ValueError("Requirements are missing")
        return "clarify" if supervisor.missing_required_fields(requirements) else "plan"

    builder = StateGraph(PlanningState)
    builder.add_node("start_run", start_run)
    builder.add_node("parse_requirements", parse_requirements)
    builder.add_node("clarify", clarify)
    builder.add_node("await_clarification", await_clarification)
    builder.add_node("plan", plan)
    builder.add_node("execute", execute)
    builder.add_node("validate_and_repair", validate_and_repair_node)
    builder.add_node("finalize", finalize)
    builder.add_edge(START, "start_run")
    builder.add_edge("start_run", "parse_requirements")
    builder.add_conditional_edges(
        "parse_requirements",
        after_requirements,
        {"clarify": "clarify", "plan": "plan"},
    )
    builder.add_edge("clarify", "await_clarification")
    builder.add_conditional_edges(
        "await_clarification",
        after_requirements,
        {"clarify": "clarify", "plan": "plan"},
    )
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", "validate_and_repair")
    builder.add_edge("validate_and_repair", "finalize")
    builder.add_edge("finalize", END)
    saver = cast(BaseCheckpointSaver[str], checkpointer)
    return builder.compile(checkpointer=saver)


def _budget_from_results(
    results: Mapping[str, object],
) -> BudgetReport | None:
    """Restore the optional budget report used by validation."""
    value = results.get("budget")
    if not isinstance(value, AgentResult) or value.data is None:
        return None
    return BudgetReport.model_validate(value.data)
