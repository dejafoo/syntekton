"""Budget defaults, policy loading, and planner clamps."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import (
    RunBudget,
    TaskBudget,
    TaskBudgetDefaults,
    clamp_task_budget,
    run_budget_from_policy,
)


def test_run_budget_defaults_match_live_run_needs() -> None:
    budget = RunBudget()
    assert budget.max_input_tokens == 3_000_000
    assert budget.max_tool_calls == 250
    assert budget.max_output_tokens == 300_000


def test_task_budget_defaults_fit_small_model_windows() -> None:
    budget = TaskBudget()
    assert budget.max_input_tokens == 28_000
    assert budget.max_tool_calls == 40


def test_policies_yaml_loads_budget_section() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    assert config.policies.budgets.run.max_input_tokens == 3_000_000
    assert config.policies.budgets.run.max_tool_calls == 250
    assert config.policies.budgets.task.min_tool_calls == 25
    assert config.policies.budgets.task.max_input_tokens_cap == 48_000


def test_run_budget_from_policy_uses_config_ceilings() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    budget = run_budget_from_policy(
        max_cost_usd="10.0",
        budgets=config.policies.budgets,
    )
    assert budget.max_cost_usd == Decimal("10.0")
    assert budget.max_input_tokens == 3_000_000
    assert budget.max_tool_calls == 250


def test_clamp_raises_starved_tool_calls_and_caps_input() -> None:
    starved = TaskBudget(
        max_input_tokens=200_000,
        max_output_tokens=30_000,
        max_tool_calls=5,
        max_repair_attempts=1,
        max_wall_clock_seconds=300,
    )
    clamped = clamp_task_budget(starved, defaults=TaskBudgetDefaults())
    assert clamped.max_tool_calls >= 25
    assert clamped.max_input_tokens <= 48_000
    assert clamped.max_input_tokens == 48_000
