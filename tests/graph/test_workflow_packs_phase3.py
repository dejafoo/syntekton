"""Graph tests for P3.D workflow packs (investigation + technical_plan alias)."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.validation.pipeline import (
    validate_acceptance_verification_links,
    validate_citations,
    validate_investigation_document,
    validate_investigation_provenance,
    validate_no_invented_defaults,
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
    assert manifest.metadata.get("workflow_pack_version") == "2.0.0"

    run_dir = tmp_path / ".product-factory" / "runs" / manifest.run_id
    report_path = run_dir / "output" / "EVIDENCE_REPORT.md"
    assert report_path.exists()
    report = report_path.read_text(encoding="utf-8")
    assert validate_investigation_document(report).status == "pass"
    assert validate_investigation_provenance(report).status == "pass"
    assert validate_citations(report).status == "pass"

    tool_names = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
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


def test_brief_to_investigation_to_plan_uses_artifact_hash_pins(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    common = {
        "repository_path": fixture,
        "budget": RunBudget(max_cost_usd=Decimal("2.00")),
        "approval_policy": "none",
        "metadata": {"disable_review": "true", "planner_mode": "fixed"},
    }
    intake = coord.run(
        RunRequest(
            request_id="req-chain-intake",
            workflow_type="change_intake",
            request_text=(
                "Add a deterministic health endpoint. Acceptance criteria: GET /health "
                "returns 200 and a stable JSON body. Non-goals: deployment changes."
            ),
            pack_input={
                "desired_outcome": "A deterministic health endpoint",
                "known_constraints": ["Use the existing API module"],
            },
            **common,
        )
    )
    assert intake.final_status == "completed", intake.notes
    intake_output = tmp_path / ".product-factory" / "runs" / intake.run_id / "output"
    brief_bytes = (intake_output / "CHANGE_BRIEF.md").read_bytes()
    brief_digest = hashlib.sha256(brief_bytes).hexdigest()
    brief_ref = HandoffRef(
        schema_id="change_brief.v1",
        digest=brief_digest,
        producer_run_id=intake.run_id,
        producer_task_id="T-003",
        role="change_brief",
        state="approved",
    )

    investigation = coord.run(
        RunRequest(
            request_id="req-chain-investigation",
            workflow_type="repository_investigation",
            request_text="Investigate the pinned health endpoint brief.",
            handoff_refs=[brief_ref],
            pack_input={"repository_revision": "fixture-commit"},
            **common,
        )
    )
    assert investigation.final_status == "completed", investigation.notes
    investigation_output = tmp_path / ".product-factory" / "runs" / investigation.run_id / "output"
    report_bytes = (investigation_output / "EVIDENCE_REPORT.md").read_bytes()
    evidence_digest = hashlib.sha256(report_bytes).hexdigest()
    evidence_ref = HandoffRef(
        schema_id="evidence_report.document.v2",
        digest=evidence_digest,
        producer_run_id=investigation.run_id,
        producer_task_id="T-002",
        role="evidence_report",
        state="evidence_complete",
    )

    plan = coord.run(
        RunRequest(
            request_id="req-chain-plan",
            workflow_type="technical_plan",
            request_text="Plan only from the pinned brief and evidence report.",
            handoff_refs=[brief_ref, evidence_ref],
            **common,
        )
    )
    assert plan.final_status == "completed", plan.notes
    plan_output = tmp_path / ".product-factory" / "runs" / plan.run_id / "output"
    document = (plan_output / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert brief_digest in document
    assert evidence_digest in document
    assert validate_acceptance_verification_links(document).status == "pass"
    assert validate_no_invented_defaults(document).status == "pass"
