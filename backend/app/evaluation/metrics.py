"""Deterministic metrics for evaluating a completed planning run."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.agent_io import BudgetReport, ItineraryPlan, ValidationReport


class EvaluationMetrics(BaseModel):
    """Comparable quality and resource metrics for one planning run."""

    budget_accuracy: float = Field(ge=0, le=1)
    constraint_violations_caught: int = Field(ge=0)
    constraint_violations_fixed: int = Field(ge=0)
    verified_place_ratio: float = Field(ge=0, le=1)
    total_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated_cost_usd: Decimal = Field(ge=0)


def compute_metrics(
    *,
    budget: BudgetReport | None,
    itinerary: ItineraryPlan | None,
    validation: ValidationReport | None,
    task_states: dict[str, dict[str, Any]] | None = None,
    initial_issue_count: int = 0,
) -> EvaluationMetrics:
    """Compute quality and resource metrics without provider-specific assumptions."""
    budget_accuracy = 1.0
    if budget and budget.total_budget:
        budget_accuracy = float(
            max(
                Decimal("0"),
                Decimal("1")
                - abs(budget.total_estimated - budget.total_budget)
                / budget.total_budget,
            )
        )
    items = [
        item
        for day in (itinerary.days if itinerary else [])
        for item in day.items
        if item.place_id
    ]
    verified_ratio = (
        sum(int(item.verified) for item in items) / len(items) if items else 0.0
    )
    states = task_states or {}
    latency = sum(int(state.get("duration_ms") or 0) for state in states.values())
    tokens = sum(
        int((state.get("token_usage") or {}).get("total_tokens", 0))
        for state in states.values()
    )
    cost = sum(
        Decimal(str((state.get("token_usage") or {}).get("estimated_cost_usd", 0)))
        for state in states.values()
    )
    caught = len(validation.issues) if validation else 0
    return EvaluationMetrics(
        budget_accuracy=max(0.0, min(1.0, budget_accuracy)),
        constraint_violations_caught=caught,
        constraint_violations_fixed=max(0, initial_issue_count - caught),
        verified_place_ratio=verified_ratio,
        total_latency_ms=latency,
        total_tokens=tokens,
        estimated_cost_usd=cost,
    )
