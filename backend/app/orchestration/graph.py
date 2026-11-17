"""LangGraph workflow for requirement extraction and task execution."""

from __future__ import annotations

from typing import cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.models.enums import AgentRunStatus
from app.orchestration.events import EventType, WorkflowEvent
from app.orchestration.executor import WorkflowExecutor
from app.orchestration.state import PlanningState
from app.orchestration.supervisor import SupervisorAgent


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
    builder.add_edge("execute", "finalize")
    builder.add_edge("finalize", END)
    saver = cast(BaseCheckpointSaver[str], checkpointer)
    return builder.compile(checkpointer=saver)
