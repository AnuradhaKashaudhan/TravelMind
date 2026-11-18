"""Deterministic budget allocation and optimization for Phase 6."""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from app.agents.base_research import ResearchAgent
from app.models.enums import AgentRunStatus, BudgetCategoryType
from app.orchestration.agent_base import AgentContext
from app.schemas.agent_io import (
    AccommodationOutput,
    ActivityOutput,
    AgentResult,
    BudgetAllocation,
    BudgetReport,
    TransportOutput,
    TripRequirements,
)

MONEY = Decimal("0.01")
ModelT = TypeVar("ModelT", bound=BaseModel)
DEFAULT_RATIOS: dict[str, dict[BudgetCategoryType, Decimal]] = {
    "budget": {
        BudgetCategoryType.TRANSPORTATION: Decimal("0.30"),
        BudgetCategoryType.ACCOMMODATION: Decimal("0.30"),
        BudgetCategoryType.FOOD: Decimal("0.20"),
        BudgetCategoryType.ACTIVITIES: Decimal("0.15"),
        BudgetCategoryType.MISCELLANEOUS: Decimal("0.05"),
    },
    "standard": {
        BudgetCategoryType.TRANSPORTATION: Decimal("0.25"),
        BudgetCategoryType.ACCOMMODATION: Decimal("0.35"),
        BudgetCategoryType.FOOD: Decimal("0.20"),
        BudgetCategoryType.ACTIVITIES: Decimal("0.15"),
        BudgetCategoryType.MISCELLANEOUS: Decimal("0.05"),
    },
    "luxury": {
        BudgetCategoryType.TRANSPORTATION: Decimal("0.20"),
        BudgetCategoryType.ACCOMMODATION: Decimal("0.45"),
        BudgetCategoryType.FOOD: Decimal("0.20"),
        BudgetCategoryType.ACTIVITIES: Decimal("0.10"),
        BudgetCategoryType.MISCELLANEOUS: Decimal("0.05"),
    },
}


def load_allocation_ratios(
    path: str | Path | None = None,
) -> dict[str, dict[BudgetCategoryType, Decimal]]:
    """Load travel-style ratios from JSON, falling back to safe defaults."""
    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[1] / "tools" / "budget_allocations.json"
    )
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return {
            style: {
                BudgetCategoryType(category): Decimal(str(ratio))
                for category, ratio in values.items()
            }
            for style, values in raw.items()
        }
    except (OSError, ValueError, TypeError):
        return DEFAULT_RATIOS


def money(value: Decimal | int | float | str | None) -> Decimal:
    """Convert a numeric value to stable two-decimal Decimal money."""
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def trip_nights(requirements: TripRequirements) -> int:
    """Return at least one night for incomplete or same-day requirements."""
    if requirements.start_date and requirements.end_date:
        return max(1, (requirements.end_date - requirements.start_date).days)
    return 1


class BudgetOptimizer:
    """Pure Decimal budget calculator with configurable style ratios."""

    def __init__(
        self,
        ratios: dict[str, dict[BudgetCategoryType, Decimal]] | None = None,
        *,
        food_per_traveler_day: Decimal = Decimal("25"),
        contingency_ratio: Decimal = Decimal("0.05"),
        currency_rates: dict[tuple[str, str], Decimal] | None = None,
    ) -> None:
        """Configure allocation ratios, food heuristics, and static FX rates."""
        self.ratios = ratios or load_allocation_ratios()
        self.food_per_traveler_day = food_per_traveler_day
        self.contingency_ratio = contingency_ratio
        self.currency_rates = currency_rates or {}

    def allocation(self, requirements: TripRequirements) -> list[BudgetAllocation]:
        """Allocate the total budget without using floating-point arithmetic."""
        budget = requirements.budget or Decimal("0")
        style = (requirements.travel_style or "standard").casefold()
        ratios = self.ratios.get(style, self.ratios["standard"])
        return [
            BudgetAllocation(
                category=category,
                allocated_amount=money(budget * ratio),
                estimated_amount=Decimal("0"),
            )
            for category, ratio in ratios.items()
        ]

    def estimate(
        self,
        requirements: TripRequirements,
        upstream: dict[str, AgentResult],
        *,
        include_tentative: bool = False,
    ) -> dict[BudgetCategoryType, Decimal]:
        """Estimate category totals from typed upstream research results."""
        travelers = Decimal(requirements.traveler_count or 1)
        nights = Decimal(trip_nights(requirements))
        transport = _typed_data(upstream.get("transport"), TransportOutput)
        accommodation = _typed_data(upstream.get("accommodation"), AccommodationOutput)
        activities = _typed_data(upstream.get("activity"), ActivityOutput)
        transport_total = Decimal("0")
        if transport and transport.options:
            transport_total = min(
                self._convert(
                    option.estimated_cost_min or option.estimated_cost or Decimal("0"),
                    option.currency,
                    requirements.currency,
                )
                for option in transport.options
            )
        accommodation_total = Decimal("0")
        if accommodation and accommodation.options:
            options = [
                option
                for option in accommodation.options
                if option.verified
                or (include_tentative and option.verification_status == "not_found")
            ]
            if options:
                accommodation_total = (
                    min(
                        self._convert(
                            option.estimated_cost_min
                            or option.estimated_cost
                            or Decimal("0"),
                            option.currency,
                            requirements.currency,
                        )
                        for option in options
                    )
                    * nights
                )
        activity_total = (
            sum(
                (
                    self._convert(
                        item.estimated_cost or Decimal("0"),
                        item.currency,
                        requirements.currency,
                    )
                    for item in activities.activities
                    if item.verified
                    or (
                        include_tentative
                        and item.verification_status in {"not_found", "unavailable"}
                    )
                ),
                Decimal("0"),
            )
            if activities
            else Decimal("0")
        )
        food_total = self.food_per_traveler_day * travelers * (nights + Decimal("1"))
        subtotal = transport_total + accommodation_total + activity_total + food_total
        return {
            BudgetCategoryType.TRANSPORTATION: transport_total,
            BudgetCategoryType.ACCOMMODATION: accommodation_total,
            BudgetCategoryType.FOOD: food_total,
            BudgetCategoryType.ACTIVITIES: activity_total,
            BudgetCategoryType.MISCELLANEOUS: money(subtotal * self.contingency_ratio),
        }

    def _convert(
        self,
        value: Decimal,
        source_currency: str | None,
        target_currency: str | None,
    ) -> Decimal:
        """Convert with a configured static rate and preserve estimate semantics."""
        source = (source_currency or target_currency or "USD").upper()
        target = (target_currency or source).upper()
        if source == target:
            return money(value)
        rate = self.currency_rates.get((source, target))
        if rate is None:
            return money(value)
        return money(value * rate)

    def build_report(
        self,
        requirements: TripRequirements,
        upstream: dict[str, AgentResult],
        *,
        what_if: str | None = None,
        include_tentative: bool = False,
    ) -> BudgetReport:
        """Build a report and greedily reduce optional costs when necessary."""
        budget = requirements.budget or Decimal("0")
        allocations = self.allocation(requirements)
        estimated = self.estimate(
            requirements,
            upstream,
            include_tentative=include_tentative,
        )
        total = sum(estimated.values(), Decimal("0"))
        selected = self._selected(
            upstream,
            include_tentative=include_tentative,
        )
        savings = self._savings(
            requirements,
            upstream,
            include_tentative=include_tentative,
            original_total=total,
        )
        suggestions: list[str] = []
        if what_if:
            total_budget = _what_if_budget(budget, what_if)
            budget = total_budget
            allocations = self.allocation(
                requirements.model_copy(update={"budget": total_budget})
            )
        if total > budget:
            suggestions = [
                "Choose the lowest-cost feasible transport option.",
                "Use the lowest-ranked accommodation within the nightly cap.",
                "Remove optional activities with the lowest value per cost.",
            ]
        return BudgetReport(
            currency=requirements.currency or "USD",
            total_budget=money(budget),
            allocations=[
                item.model_copy(
                    update={"estimated_amount": money(estimated[item.category])}
                )
                for item in allocations
            ],
            total_allocated=money(
                sum(
                    (item.allocated_amount for item in allocations),
                    Decimal("0"),
                )
            ),
            total_estimated=money(total),
            over_budget=total > budget,
            suggestions=suggestions,
            selected=selected,
            savings_achieved=savings,
            breakdown={
                category: [f"{category.value}: {money(value)}"]
                for category, value in estimated.items()
            },
            what_if=what_if,
        )

    def _savings(
        self,
        requirements: TripRequirements,
        upstream: dict[str, AgentResult],
        *,
        include_tentative: bool = False,
        original_total: Decimal,
    ) -> Decimal:
        """Report savings from choosing minima instead of the highest estimates."""
        savings = Decimal("0")
        transport = _typed_data(upstream.get("transport"), TransportOutput)
        if transport and transport.options:
            values = [
                self._convert(
                    option.estimated_cost_max or option.estimated_cost or Decimal("0"),
                    option.currency,
                    requirements.currency,
                )
                for option in transport.options
            ]
            if values:
                savings += max(values) - min(values)
        accommodation = _typed_data(upstream.get("accommodation"), AccommodationOutput)
        if accommodation and accommodation.options:
            options = [
                option
                for option in accommodation.options
                if option.verified
                or (
                    include_tentative
                    and option.verification_status in {"not_found", "unavailable"}
                )
            ]
            values = [
                self._convert(
                    option.estimated_cost_max or option.estimated_cost or Decimal("0"),
                    option.currency,
                    requirements.currency,
                )
                for option in options
            ]
            if values:
                savings += (max(values) - min(values)) * trip_nights(requirements)
        return money(min(savings, original_total))

    def _original_estimated_total(
        self,
        requirements: TripRequirements,
        upstream: dict[str, AgentResult],
    ) -> Decimal:
        """Return the baseline cost using each category's original maximum choice."""
        original = Decimal("0")
        transport = _typed_data(upstream.get("transport"), TransportOutput)
        if transport and transport.options:
            original += max(
                (
                    self._convert(
                        option.estimated_cost_max
                        or option.estimated_cost
                        or Decimal("0"),
                        option.currency,
                        requirements.currency,
                    )
                    for option in transport.options
                ),
                default=Decimal("0"),
            )
        accommodation = _typed_data(upstream.get("accommodation"), AccommodationOutput)
        if accommodation and accommodation.options:
            original += max(
                (
                    self._convert(
                        option.estimated_cost_max
                        or option.estimated_cost
                        or Decimal("0"),
                        option.currency,
                        requirements.currency,
                    )
                    for option in accommodation.options
                ),
                default=Decimal("0"),
            ) * trip_nights(requirements)
        activities = _typed_data(upstream.get("activity"), ActivityOutput)
        if activities:
            original += sum(
                (
                    self._convert(
                        item.estimated_cost or Decimal("0"),
                        item.currency,
                        requirements.currency,
                    )
                    for item in activities.activities
                    if item.verified
                ),
                Decimal("0"),
            )
        original += (
            self.food_per_traveler_day
            * (requirements.traveler_count or 1)
            * (trip_nights(requirements) + 1)
        )
        return money(original)

    def _selected(
        self,
        upstream: dict[str, AgentResult],
        *,
        include_tentative: bool = False,
    ) -> dict[BudgetCategoryType, list[str]]:
        """Return the currently cheapest selected research choices."""
        selected: dict[BudgetCategoryType, list[str]] = {}
        transport = _typed_data(upstream.get("transport"), TransportOutput)
        if transport and transport.options:
            selected[BudgetCategoryType.TRANSPORTATION] = [
                min(
                    transport.options,
                    key=lambda option: option.estimated_cost_min or Decimal("0"),
                ).mode
            ]
        accommodation = _typed_data(upstream.get("accommodation"), AccommodationOutput)
        if accommodation and accommodation.options:
            options = [
                option
                for option in accommodation.options
                if option.verified
                or (
                    include_tentative
                    and option.verification_status in {"not_found", "unavailable"}
                )
            ]
            if options:
                selected[BudgetCategoryType.ACCOMMODATION] = [
                    min(
                        options,
                        key=lambda option: option.estimated_cost_min or Decimal("0"),
                    ).name
                ]
        activities = _typed_data(upstream.get("activity"), ActivityOutput)
        if activities:
            selected[BudgetCategoryType.ACTIVITIES] = [
                item.title
                for item in activities.activities
                if item.verified
                or (
                    include_tentative
                    and item.verification_status in {"not_found", "unavailable"}
                )
            ]
        return selected


class BudgetOptimizationAgent(ResearchAgent[BudgetReport]):
    """Create a deterministic budget report from research outputs."""

    name = "budget"
    description = "Deterministic Decimal budget allocation and optimization."
    output_model = BudgetReport
    prompt_name = "budget_optimization_v1.txt"

    def __init__(self, optimizer: BudgetOptimizer | None = None) -> None:
        """Configure the pure budget calculator."""
        self.optimizer = optimizer or BudgetOptimizer()

    async def run(self, context: AgentContext) -> AgentResult:
        """Return a typed budget report; LLM text is optional and non-authoritative."""
        requirements = self.requirements(context)
        report = self.optimizer.build_report(
            requirements,
            context.upstream_results,
            what_if=context.task.inputs.get("what_if"),
            include_tentative=bool(context.task.inputs.get("include_unverified")),
        )
        return AgentResult(
            agent_name=self.name,
            status=AgentRunStatus.SUCCEEDED,
            data=report.model_dump(mode="json"),
            sources=["deterministic-budget"],
            confidence=1.0,
        )


def _typed_data(result: AgentResult | None, model: type[ModelT]) -> ModelT | None:
    """Validate an upstream envelope into its declared typed output."""
    if result is None or result.data is None:
        return None
    try:
        return model.model_validate(result.data)
    except Exception:
        return None


def _what_if_budget(budget: Decimal, constraint: str) -> Decimal:
    """Apply a bounded percentage-reduction what-if constraint."""
    text = constraint.casefold().replace("%", "")
    try:
        digits = "".join(
            character for character in text if character.isdigit() or character == "."
        )
        percent = Decimal(digits)
    except ArithmeticError:
        return budget
    return money(budget * (Decimal("1") - percent / Decimal("100")))
