"""Unit tests for domain contracts."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from product_factory.domain import export_json_schemas
from product_factory.domain.budgets import RunBudget, TaskBudget
from product_factory.domain.findings import Finding
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec


def test_run_budget_decimal_not_float() -> None:
    budget = RunBudget(max_cost_usd="3.00")
    assert isinstance(budget.max_cost_usd, Decimal)
    assert budget.remaining_cost(Decimal("1.25")) == Decimal("1.75")
    assert budget.would_exceed_cost(Decimal("2.5"), Decimal("1.0"))


def test_task_budget_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        TaskBudget(
            max_input_tokens=-1,
            max_output_tokens=1,
            max_tool_calls=1,
            max_repair_attempts=0,
            max_wall_clock_seconds=1,
        )


def test_run_request_round_trip() -> None:
    req = RunRequest(
        request_id="r1",
        workflow_type="code_change",
        request_text="add health endpoint",
        budget=RunBudget(max_cost_usd="2.50"),
    )
    data = req.model_dump(mode="json")
    again = RunRequest.model_validate(data)
    assert again.budget.max_cost_usd == Decimal("2.50")


def test_finding_requires_bounds() -> None:
    with pytest.raises(ValidationError):
        Finding(
            id="f1",
            category="correctness",
            severity="blocking",
            summary="x",
            explanation="y",
            confidence=1.5,
            produced_by="test",
        )


def test_export_json_schemas(tmp_path: Path) -> None:
    written = export_json_schemas(tmp_path)
    assert len(written) >= 8
    assert (tmp_path / "PlannerOutput.schema.json").exists()


def test_planner_output_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        PlannerOutput.model_validate(
            {
                "objective": "x",
                "tasks": [],
                "extra_field": True,
            }
        )


def test_task_spec_minimal() -> None:
    task = TaskSpec(
        id="T-1",
        title="t",
        capability="implementation",
        objective="do it",
        expected_output_schema="x.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="a", description="d", verification="test_suite")
        ],
    )
    assert task.risk == "low"
