"""Budget models and Decimal arithmetic."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


def parse_decimal(value: Decimal | int | float | str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # Avoid binary float artifacts by going through str.
        return Decimal(str(value))
    return Decimal(value)


class TaskBudget(BaseModel):
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    max_tool_calls: int = Field(ge=0)
    max_repair_attempts: int = Field(ge=0)
    max_wall_clock_seconds: int = Field(ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("1.00"))

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal:
        return parse_decimal(v)  # type: ignore[arg-type]


class RunBudget(BaseModel):
    max_cost_usd: Decimal = Field(default=Decimal("3.00"))
    max_input_tokens: int = 1_000_000
    max_output_tokens: int = 150_000
    max_tasks: int = 20
    max_parallel_tasks: int = 3
    max_tool_calls: int = 100
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
