"""Integration tests: budget exhaustion mid-run yields a typed terminal status (P1.A)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import BudgetExhaustedError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration import budget_ledger as budget_ledger_module
from product_factory.orchestration.coordinator import RunCoordinator
from tests.conftest import clone_fixture


def _coord(tmp_path: Path) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    return RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def _fixture(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    return clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def test_tool_call_budget_exhausted_mid_run_sets_typed_terminal_status(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    fixture = _fixture(tmp_path)
    request = RunRequest(
        request_id="req-tool-budget",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00"), max_tool_calls=1),
    )
    with pytest.raises(BudgetExhaustedError) as excinfo:
        coord.run(request)
    assert excinfo.value.details["dimension"] == "max_tool_calls"

    rows = coord.db.list_runs()
    assert len(rows) == 1
    assert rows[0]["status"] == "budget_exhausted"


def test_wall_clock_budget_exhausted_mid_run_sets_typed_terminal_status(
    tmp_path: Path, monkeypatch
) -> None:
    coord = _coord(tmp_path)
    fixture = _fixture(tmp_path)

    # Simulate wall-clock exhaustion deterministically: the run starts at t=0,
    # then every subsequent `time.monotonic()` call (budget checks) reports a
    # time far past any reasonable `max_wall_clock_seconds`.
    calls = {"n": 0}
    real_monotonic = budget_ledger_module.time.monotonic

    def fake_monotonic() -> float:
        calls["n"] += 1
        if calls["n"] <= 1:
            return real_monotonic()
        return real_monotonic() + 10_000

    monkeypatch.setattr(budget_ledger_module.time, "monotonic", fake_monotonic)

    request = RunRequest(
        request_id="req-wallclock-budget",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00"), max_wall_clock_seconds=60),
    )
    with pytest.raises(BudgetExhaustedError) as excinfo:
        coord.run(request)
    assert excinfo.value.details["dimension"] == "max_wall_clock_seconds"

    rows = coord.db.list_runs()
    assert len(rows) == 1
    assert rows[0]["status"] == "budget_exhausted"


def test_cost_budget_exhausted_before_any_task(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    fixture = _fixture(tmp_path)
    request = RunRequest(
        request_id="req-cost-budget",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("0.00")),
    )
    with pytest.raises(BudgetExhaustedError):
        coord.run(request)
    rows = coord.db.list_runs()
    assert rows[0]["status"] == "budget_exhausted"
