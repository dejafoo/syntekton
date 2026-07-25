"""P4.A graph tests — a run delivers the deliverable name the host asked for."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.validation.pipeline import (
    validate_architecture_document,
    validate_investigation_document,
)
from tests.conftest import clone_fixture


def _coord(tmp_path: Path) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    return RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_technical_plan_honors_requested_deliverable_name(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-named-arch",
            workflow_type="technical_plan",
            request_text="Design an integration testing architecture and rollout plan.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            artifact_overrides={
                "architecture_document": ArtifactOverride(
                    dest_path="docs/integration_testing_architecture.md"
                )
            },
        )
    )
    assert manifest.final_status == "completed"

    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    named = output / "integration_testing_architecture.md"
    assert named.is_file()
    assert not (output / "ARCHITECTURE.md").exists()

    document = named.read_text(encoding="utf-8")
    # Renaming must not change whether the document passes section validation.
    assert validate_architecture_document(document).status == "pass"
    assert document.startswith("# integration_testing_architecture.md")

    stored = {row["logical_name"] for row in coord.db.list_artifacts()}
    assert "integration_testing_architecture.md" in stored
    assert "ARCHITECTURE.md" not in stored


def test_technical_plan_default_name_is_unchanged(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-default-arch",
            workflow_type="technical_plan",
            request_text="Design a small SaaS billing service.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )
    assert manifest.final_status == "completed"
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    assert (output / "ARCHITECTURE.md").is_file()


def test_investigation_honors_requested_report_name(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-named-report",
            workflow_type="repository_investigation",
            request_text="Map the sample API entry points and test layout.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            artifact_overrides={
                "evidence_report": ArtifactOverride(dest_path="docs/api_surface_review.md")
            },
        )
    )
    assert manifest.final_status == "completed"
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    report = output / "api_surface_review.md"
    assert report.is_file()
    assert not (output / "EVIDENCE_REPORT.md").exists()
    assert validate_investigation_document(report.read_text(encoding="utf-8")).status == "pass"


def test_pack_resolved_event_publishes_land_map(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-land-map-event",
            workflow_type="technical_plan",
            request_text="Design a caching layer.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            artifact_overrides={
                "architecture_document": ArtifactOverride(dest_path="docs/caching_design.md")
            },
        )
    )
    events = coord.db.list_events(run_id=manifest.run_id, after_seq=0, limit=500)
    resolved = [e for e in events if e["event_type"] == "workflow.pack_resolved"]
    assert resolved, "expected a workflow.pack_resolved event"
    payload = json.loads(resolved[0]["payload_json"])
    land_map = payload["artifact_land_map"]
    assert land_map[0]["logical_name"] == "caching_design.md"
    assert land_map[0]["suggested_dest_path"] == "docs/caching_design.md"
