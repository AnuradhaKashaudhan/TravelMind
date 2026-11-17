"""Dependency graph utilities for supervisor plans."""

from __future__ import annotations

from collections import deque

from app.models.enums import AgentRunStatus
from app.orchestration.state import TaskExecutionState
from app.schemas.agent_io import SupervisorPlan, TaskSpec


def topological_order(plan: SupervisorPlan) -> list[str]:
    """Return a stable topological ordering, prioritizing lower priority values."""
    tasks = {task.task_key: task for task in plan.tasks}
    remaining = {key: set(task.depends_on) for key, task in tasks.items()}
    ordered: list[str] = []
    while remaining:
        ready = sorted(
            (key for key, dependencies in remaining.items() if not dependencies),
            key=lambda key: (tasks[key].priority, key),
        )
        if not ready:
            raise ValueError("SupervisorPlan contains a dependency cycle")
        for key in ready:
            ordered.append(key)
            remaining.pop(key)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return ordered


def ready_tasks(
    plan: SupervisorPlan,
    states: dict[str, TaskExecutionState],
) -> list[TaskSpec]:
    """Return tasks whose dependencies succeeded or optionally failed/skipped."""
    tasks = {task.task_key: task for task in plan.tasks}
    ready = [
        task
        for task in plan.tasks
        if states.get(task.task_key, {}).get("status", AgentRunStatus.PENDING)
        == AgentRunStatus.PENDING
        and all(
            states.get(dependency, {}).get("status") == AgentRunStatus.SUCCEEDED
            or (
                tasks[dependency].optional
                and states.get(dependency, {}).get("status")
                in {AgentRunStatus.FAILED, AgentRunStatus.SKIPPED}
            )
            for dependency in task.depends_on
        )
    ]
    return sorted(ready, key=lambda task: (task.priority, task.task_key))


def blocked_tasks(
    plan: SupervisorPlan,
    states: dict[str, TaskExecutionState],
) -> list[TaskSpec]:
    """Return pending tasks with a failed or skipped required dependency."""
    tasks = {task.task_key: task for task in plan.tasks}
    blocked = [
        task
        for task in plan.tasks
        if states.get(task.task_key, {}).get("status", AgentRunStatus.PENDING)
        == AgentRunStatus.PENDING
        and any(
            states.get(dependency, {}).get("status")
            in {AgentRunStatus.FAILED, AgentRunStatus.SKIPPED}
            and not tasks[dependency].optional
            for dependency in task.depends_on
        )
    ]
    return sorted(blocked, key=lambda task: (task.priority, task.task_key))


def affected_downstream(plan: SupervisorPlan, task_keys: set[str]) -> set[str]:
    """Return all transitive dependents of the supplied task keys."""
    dependents: dict[str, set[str]] = {task.task_key: set() for task in plan.tasks}
    for task in plan.tasks:
        for dependency in task.depends_on:
            dependents[dependency].add(task.task_key)
    found = set(task_keys)
    queue = deque(task_keys)
    while queue:
        for dependent in dependents.get(queue.popleft(), set()):
            if dependent not in found:
                found.add(dependent)
                queue.append(dependent)
    return found - task_keys
