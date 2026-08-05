"""Budget models and Decimal arithmetic."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator


def parse_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Avoid binary float artifacts by going through str.
        return Decimal(str(value))
    return Decimal(value)


class TaskBudget(BaseModel):
    # Sized for typical worker context windows (see config/models.yaml soft limits).
    max_input_tokens: int = Field(default=28_000, ge=0)
    max_output_tokens: int = Field(default=8_000, ge=0)
    # Live coding/repair loops need room; planner-invented 5-call budgets starve.
    max_tool_calls: int = Field(default=40, ge=0)
    max_repair_attempts: int = Field(default=2, ge=0)
    max_wall_clock_seconds: int = Field(default=600, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"))

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal:
        return parse_decimal(v)  # type: ignore[arg-type]


class RunBudget(BaseModel):
    max_cost_usd: Decimal = Field(default=Decimal("3.00"))
    # Cumulative across the whole run (every model round counts). Live technical
    # plans with repairs easily exceed 1M; keep a firm ceiling, not a tripwire.
    max_input_tokens: int = 3_000_000
    max_output_tokens: int = 300_000
    max_tasks: int = 20
    max_parallel_tasks: int = 3
    max_tool_calls: int = 250
    max_plan_repairs: int = 1
    max_task_repairs: int = 2
    max_total_repair_tasks: int = 6
    max_wall_clock_seconds: int = 1800
    max_command_seconds: int = 300

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal:
        return parse_decimal(v)  # type: ignore[arg-type]

    def remaining_cost(self, spent: Decimal) -> Decimal:
        spent_d = parse_decimal(spent)
        remaining = self.max_cost_usd - spent_d
        return remaining if remaining > 0 else Decimal("0")

    def would_exceed_cost(self, spent: Decimal, projected: Decimal) -> bool:
        return (parse_decimal(spent) + parse_decimal(projected)) > self.max_cost_usd

    def add_usage(
        self,
        spent: Decimal,
        additional: Decimal,
    ) -> Decimal:
        return parse_decimal(spent) + parse_decimal(additional)


class RunBudgetDefaults(BaseModel):
    """Operator-tunable run ceilings (from config/policies.yaml)."""

    max_input_tokens: int = 3_000_000
    max_output_tokens: int = 300_000
    max_tool_calls: int = 250
    max_tasks: int = 20
    max_parallel_tasks: int = 3
    max_wall_clock_seconds: int = 1800
    max_command_seconds: int = 300
    max_plan_repairs: int = 1
    max_task_repairs: int = 2
    max_total_repair_tasks: int = 6


class TaskBudgetDefaults(BaseModel):
    """Defaults + clamps applied to each planned task."""

    max_input_tokens: int = 28_000
    max_output_tokens: int = 8_000
    max_tool_calls: int = 40
    min_tool_calls: int = 25
    max_input_tokens_cap: int = 48_000
    max_repair_attempts: int = 2
    max_wall_clock_seconds: int = 600
    max_cost_usd: Decimal = Field(default=Decimal("1.00"))

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal:
        return parse_decimal(v)  # type: ignore[arg-type]


class BudgetsConfig(BaseModel):
    run: RunBudgetDefaults = Field(default_factory=RunBudgetDefaults)
    task: TaskBudgetDefaults = Field(default_factory=TaskBudgetDefaults)


def run_budget_from_policy(
    *,
    max_cost_usd: Decimal | float | str,
    budgets: BudgetsConfig | RunBudgetDefaults | None = None,
    **overrides: Any,
) -> RunBudget:
    """Build a RunBudget from policy defaults, with cost and optional overrides."""
    run_defaults = (
        budgets.run if isinstance(budgets, BudgetsConfig) else (budgets or RunBudgetDefaults())
    )
    data = run_defaults.model_dump()
    data["max_cost_usd"] = parse_decimal(max_cost_usd)
    data.update({key: value for key, value in overrides.items() if value is not None})
    return RunBudget(**data)


def clamp_task_budget(
    budget: TaskBudget,
    *,
    defaults: TaskBudgetDefaults | None = None,
) -> TaskBudget:
    """Bound a planner- or pack-invented task budget to sane floors/ceilings.

    - Caps input tokens so prompt packing stays near typical model windows.
    - Raises tool-call floors so implementation/repair loops are not starved.
    """
    policy = defaults or TaskBudgetDefaults()
    return budget.model_copy(
        update={
            "max_input_tokens": min(
                max(budget.max_input_tokens, 1),
                policy.max_input_tokens_cap,
            ),
            "max_output_tokens": min(
                max(budget.max_output_tokens, 1),
                max(policy.max_output_tokens * 2, policy.max_output_tokens),
            ),
            "max_tool_calls": max(budget.max_tool_calls, policy.min_tool_calls),
        }
    )
