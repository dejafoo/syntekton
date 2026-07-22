"""Graph and vertical-slice tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.graph import build_graph
from tests.conftest import clone_fixture


def test_graph_compiles_and_runs() -> None:
    graph = build_graph()
    result = graph.invoke(
        {
            "run_id": "run-test",
            "workflow_type": "code_change",
            "compiler_errors": [],
            "validation_results": [],
            "plan_attempt": 0,
            "repair_count": 0,
            "task_results": [],
            "findings": [],
            "events": [],
            "final_status": "initializing",
        },
        config={"configurable": {"thread_id": "t1"}},
    )
    assert result["final_status"] in {"completed", "awaiting_approval"}


def test_code_change_vertical_slice(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coord.run(
        RunRequest(
            request_id="req-1",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        )
    )
    assert manifest.final_status == "awaiting_approval"
    run_dir = tmp_path / ".product-factory" / "runs" / manifest.run_id
    assert (run_dir / "output" / "plan.json").exists()
    assert (run_dir / "output" / "proposed.patch").exists() or (
        run_dir / "output" / "implementation.patch"
    ).exists()
    assert (run_dir / "run-manifest.json").exists()


def test_architecture_workflow(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    coord = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coord.run(
        RunRequest(
            request_id="req-2",
            workflow_type="architecture",
            request_text="Design a small SaaS billing service.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )
    assert manifest.final_status == "completed"
    run_dir = tmp_path / ".product-factory" / "runs" / manifest.run_id
    assert (run_dir / "output" / "ARCHITECTURE.md").exists()
