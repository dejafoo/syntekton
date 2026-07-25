"""Run-level budget ledger — enforce limits before billable actions."""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Literal

from product_factory.domain.budgets import RunBudget, parse_decimal
from product_factory.domain.errors import BudgetExhaustedError
from product_factory.domain.usage import UsageMetrics

BudgetDimension = Literal[
    "max_cost_usd",
    "max_input_tokens",
    "max_output_tokens",
    "max_tool_calls",
    "max_wall_clock_seconds",
    "max_command_seconds",
]


class BudgetLedger:
    """Tracks cumulative run usage and rejects actions that would exceed limits."""

    def __init__(self, budget: RunBudget, *, started_monotonic: float | None = None) -> None:
        self.budget = budget
        self.usage = UsageMetrics()
        self.tool_calls = 0
        self.command_seconds = 0.0
        self.started_monotonic = (
            started_monotonic if started_monotonic is not None else time.monotonic()
        )
        self._warned_profile_set = False

    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_monotonic)

    def check_before_model(self, *, projected_cost: Decimal = Decimal("0")) -> None:
        self._check_wall_clock()
        self._check_cost(projected_cost)
        if self.usage.input_tokens >= self.budget.max_input_tokens:
            self._fail("max_input_tokens", "Run input token budget exhausted")
        if self.usage.output_tokens >= self.budget.max_output_tokens:
            self._fail("max_output_tokens", "Run output token budget exhausted")

    def check_before_tool(self) -> None:
        self._check_wall_clock()
        self._check_cost(Decimal("0"))
        if self.tool_calls >= self.budget.max_tool_calls:
            self._fail("max_tool_calls", "Run tool-call budget exhausted")

    def check_before_command(self, *, timeout_seconds: int) -> None:
        self._check_wall_clock()
        if timeout_seconds > self.budget.max_command_seconds:
            self._fail(
                "max_command_seconds",
                f"Command timeout {timeout_seconds}s exceeds run max_command_seconds "
                f"{self.budget.max_command_seconds}",
            )
        if self.command_seconds >= float(self.budget.max_command_seconds):
            self._fail("max_command_seconds", "Run command-seconds budget exhausted")

    def check_wall_clock(self) -> None:
        """Public wall-clock check for coordinator-level loop boundaries (wave/task)."""
        self._check_wall_clock()

    def record_usage(self, usage: UsageMetrics) -> None:
        self.usage = self.usage.merge(usage)

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def record_command(self, *, duration_seconds: float) -> None:
        self.command_seconds += max(0.0, duration_seconds)

    def remaining_cost(self) -> Decimal:
        return self.budget.remaining_cost(self.usage.estimated_cost_usd)

    def snapshot(self) -> dict[str, object]:
        return {
            "usage": self.usage.model_dump(mode="json"),
            "tool_calls": self.tool_calls,
            "command_seconds": self.command_seconds,
            "elapsed_seconds": self.elapsed_seconds(),
            "budget": self.budget.model_dump(mode="json"),
        }

    def _check_wall_clock(self) -> None:
        if self.elapsed_seconds() >= float(self.budget.max_wall_clock_seconds):
            self._fail("max_wall_clock_seconds", "Run wall-clock budget exhausted")

    def _check_cost(self, projected: Decimal) -> None:
        if self.budget.would_exceed_cost(self.usage.estimated_cost_usd, projected):
            self._fail("max_cost_usd", "Run cost budget exhausted")

    def _fail(self, dimension: BudgetDimension, message: str) -> None:
        raise BudgetExhaustedError(
            message,
            details={
                "dimension": dimension,
                "ledger": self.snapshot(),
            },
        )

    @classmethod
    def restore(cls, budget: RunBudget, snapshot: dict[str, object]) -> BudgetLedger:
        """Rebuild a ledger from a persisted `snapshot()` for durable resume (P1.B).

        Cumulative usage/tool-calls/command-seconds carry over so a resumed
        run cannot exceed run-level limits by restarting; `started_monotonic`
        is shifted backward by the previously elapsed wall-clock time so the
        wall-clock budget spans the original run plus this resumed process.
        """
        ledger = cls(budget)
        usage_data = snapshot.get("usage")
        if isinstance(usage_data, dict) and usage_data:
            ledger.usage = UsageMetrics.model_validate(usage_data)
        ledger.tool_calls = int(snapshot.get("tool_calls") or 0)  # type: ignore[arg-type]
        ledger.command_seconds = float(snapshot.get("command_seconds") or 0.0)  # type: ignore[arg-type]
        elapsed = float(snapshot.get("elapsed_seconds") or 0.0)  # type: ignore[arg-type]
        ledger.started_monotonic = time.monotonic() - elapsed
        return ledger


def warn_unused_profile_set(profile_set: str) -> str | None:
    """Return a deprecation note when model_profile_set is non-default/unused."""
    if not profile_set or profile_set == "local-target":
        return None
    return (
        f"model_profile_set={profile_set!r} is ignored; routing uses capability "
        "profiles from config/models.yaml via the scheduler"
    )


def coerce_run_budget(
    *,
    max_cost_usd: Decimal | float | str,
    max_wall_clock_seconds: int | None = None,
    max_tool_calls: int | None = None,
    max_command_seconds: int | None = None,
) -> RunBudget:
    from product_factory.domain.budgets import run_budget_from_policy

    return run_budget_from_policy(
        max_cost_usd=max_cost_usd,
        max_wall_clock_seconds=max_wall_clock_seconds,
        max_tool_calls=max_tool_calls,
        max_command_seconds=max_command_seconds,
    )
