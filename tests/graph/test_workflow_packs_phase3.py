"""Graph tests for P3.D workflow packs (investigation + technical_plan alias)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.validation.pipeline import (
    validate_citations,
    validate_investigation_document,
)
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


def test_mock_investigation_produces_report_without_write_tools(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-invest-1",
            workflow_type="repository_investigation",
            request_text="Map the sample API entry points and test layout.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )
    assert manifest.final_status == "completed"
    assert manifest.metadata.get("workflow_pack_id") == "repository_investigation"
    assert manifest.metadata.get("workflow_pack_version") == "1.0.0"

    run_dir = tmp_path / ".product-factory" / "runs" / manifest.run_id
    report_path = run_dir / "output" / "EVIDENCE_REPORT.md"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert validate_investigation_document(report).status == "pass"
    assert validate_citations(report).status == "pass"

    tool_names = {
        row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)
    }
    assert "create_file" not in tool_names
    assert "apply_patch" not in tool_names
    assert tool_names & {"list_files", "read_file", "search_text", "git_diff", "git_status"}


def test_architecture_and_technical_plan_alias_parity(tmp_path: Path) -> None:
    coord_arch = _coord(tmp_path / "arch")
    coord_plan = _coord(tmp_path / "plan")
    request_text = "Design a small SaaS billing service."

    arch_manifest = coord_arch.run(
        RunRequest(
            request_id="req-arch",
            workflow_type="architecture",
            request_text=request_text,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )
    plan_manifest = coord_plan.run(
        RunRequest(
            request_id="req-plan",
            workflow_type="technical_plan",
            request_text=request_text,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )

    assert arch_manifest.final_status == "completed"
    assert plan_manifest.final_status == "completed"
    assert arch_manifest.metadata.get("workflow_pack_id") == "technical_plan"
    assert plan_manifest.metadata.get("workflow_pack_id") == "technical_plan"
    assert arch_manifest.metadata.get("workflow_pack_hash") == plan_manifest.metadata.get(
        "workflow_pack_hash"
    )

    arch_dir = tmp_path / "arch" / ".product-factory" / "runs" / arch_manifest.run_id
    plan_dir = tmp_path / "plan" / ".product-factory" / "runs" / plan_manifest.run_id
    arch_doc = (arch_dir / "output" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    plan_doc = (plan_dir / "output" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert arch_doc == plan_doc

    arch_plan = (arch_dir / "output" / "plan.json").read_text(encoding="utf-8")
    plan_plan = (plan_dir / "output" / "plan.json").read_text(encoding="utf-8")
    assert arch_plan == plan_plan
