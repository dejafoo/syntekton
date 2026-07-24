"""Unit tests for BudgetLedger (P1.A): each dimension trips independently."""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import BudgetExhaustedError
from product_factory.domain.usage import UsageMetrics
from product_factory.orchestration.budget_ledger import (
    BudgetLedger,
    coerce_run_budget,
    warn_unused_profile_set,
)


def _budget(**overrides: object) -> RunBudget:
    defaults: dict[str, object] = {
        "max_cost_usd": Decimal("1.00"),
        "max_input_tokens": 1000,
        "max_output_tokens": 1000,
        "max_tool_calls": 5,
        "max_wall_clock_seconds": 3600,
        "max_command_seconds": 60,
    }
    defaults.update(overrides)
    return RunBudget(**defaults)  # type: ignore[arg-type]


def test_cost_budget_trips_before_model_call() -> None:
    ledger = BudgetLedger(_budget(max_cost_usd=Decimal("0.01")))
    ledger.record_usage(UsageMetrics(estimated_cost_usd=Decimal("0.02")))
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_model()
    assert excinfo.value.details["dimension"] == "max_cost_usd"


def test_input_token_budget_trips() -> None:
    ledger = BudgetLedger(_budget(max_input_tokens=100))
    ledger.record_usage(UsageMetrics(input_tokens=150))
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_model()
    assert excinfo.value.details["dimension"] == "max_input_tokens"


def test_output_token_budget_trips() -> None:
    ledger = BudgetLedger(_budget(max_output_tokens=100))
    ledger.record_usage(UsageMetrics(output_tokens=150))
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_model()
    assert excinfo.value.details["dimension"] == "max_output_tokens"


def test_tool_call_budget_trips() -> None:
    ledger = BudgetLedger(_budget(max_tool_calls=2))
    ledger.record_tool_call()
    ledger.record_tool_call()
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_tool()
    assert excinfo.value.details["dimension"] == "max_tool_calls"


def test_wall_clock_budget_trips() -> None:
    ledger = BudgetLedger(_budget(max_wall_clock_seconds=1))
    ledger.started_monotonic = time.monotonic() - 10
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_wall_clock()
    assert excinfo.value.details["dimension"] == "max_wall_clock_seconds"


def test_wall_clock_checked_before_model_and_tool() -> None:
    ledger = BudgetLedger(_budget(max_wall_clock_seconds=1))
    ledger.started_monotonic = time.monotonic() - 10
    with pytest.raises(BudgetExhaustedError):
        ledger.check_before_model()
    ledger2 = BudgetLedger(_budget(max_wall_clock_seconds=1))
    ledger2.started_monotonic = time.monotonic() - 10
    with pytest.raises(BudgetExhaustedError):
        ledger2.check_before_tool()


def test_command_seconds_budget_trips_on_configured_timeout() -> None:
    ledger = BudgetLedger(_budget(max_command_seconds=30))
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_command(timeout_seconds=60)
    assert excinfo.value.details["dimension"] == "max_command_seconds"


def test_command_seconds_budget_trips_on_cumulative_usage() -> None:
    ledger = BudgetLedger(_budget(max_command_seconds=10))
    ledger.record_command(duration_seconds=10.0)
    with pytest.raises(BudgetExhaustedError) as excinfo:
        ledger.check_before_command(timeout_seconds=5)
    assert excinfo.value.details["dimension"] == "max_command_seconds"


def test_ledger_under_budget_does_not_raise() -> None:
    ledger = BudgetLedger(_budget())
    ledger.check_before_model()
    ledger.check_before_tool()
    ledger.check_before_command(timeout_seconds=5)
    ledger.record_usage(UsageMetrics(input_tokens=10, output_tokens=10))
    ledger.record_tool_call()
    ledger.record_command(duration_seconds=1.0)


def test_restore_carries_over_cumulative_usage_and_elapsed_time() -> None:
    ledger = BudgetLedger(_budget(max_tool_calls=10))
    ledger.record_usage(UsageMetrics(input_tokens=50, output_tokens=25))
    ledger.record_tool_call()
    ledger.record_command(duration_seconds=3.0)
    snapshot = ledger.snapshot()

    restored = BudgetLedger.restore(_budget(max_tool_calls=10), snapshot)
    assert restored.usage.input_tokens == 50
    assert restored.usage.output_tokens == 25
    assert restored.tool_calls == 1
    assert restored.command_seconds == 3.0
    # Elapsed time from the prior process carries forward into the new ledger.
    assert restored.elapsed_seconds() >= snapshot["elapsed_seconds"]


def test_restore_prevents_exceeding_run_budget_by_restarting() -> None:
    budget = _budget(max_tool_calls=2)
    ledger = BudgetLedger(budget)
    ledger.record_tool_call()
    ledger.record_tool_call()
    snapshot = ledger.snapshot()

    restored = BudgetLedger.restore(budget, snapshot)
    with pytest.raises(BudgetExhaustedError):
        restored.check_before_tool()


def test_warn_unused_profile_set_default_is_silent() -> None:
    assert warn_unused_profile_set("local-target") is None
    assert warn_unused_profile_set("") is None


def test_warn_unused_profile_set_non_default_warns() -> None:
    note = warn_unused_profile_set("some-other-profile")
    assert note is not None
    assert "some-other-profile" in note


def test_coerce_run_budget_applies_overrides() -> None:
    budget = coerce_run_budget(
        max_cost_usd="2.50", max_wall_clock_seconds=120, max_tool_calls=7, max_command_seconds=45
    )
    assert budget.max_cost_usd == Decimal("2.50")
    assert budget.max_wall_clock_seconds == 120
    assert budget.max_tool_calls == 7
    assert budget.max_command_seconds == 45


def test_coerce_run_budget_defaults_omitted_overrides() -> None:
    budget = coerce_run_budget(max_cost_usd=1.0)
    assert budget.max_wall_clock_seconds == RunBudget().max_wall_clock_seconds
