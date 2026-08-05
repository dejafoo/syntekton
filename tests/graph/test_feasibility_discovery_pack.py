"""Graph tests for the feasibility_discovery pack (PM1.D)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import ArtifactOverride, RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.validation.pipeline import (
    validate_feasibility_document,
    validate_option_comparison,
    validate_recommendation,
    validate_regulated_claims,
    validate_research_provenance,
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


def _pack_input(**updates: object) -> dict:
    payload = {
        "decision_statement": "Can we integrate with the public vendor webhook API?",
        "domain": "payments",
        "allow_technical_spike": False,
        "source_policy_profile": "public-technical",
    }
    payload.update(updates)
    return payload


def test_mock_discovery_lands_feasibility_dossier(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-discover-1",
            workflow_type="feasibility_discovery",
            request_text="Assess webhook integration feasibility.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
            pack_input=_pack_input(),
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    assert manifest.metadata.get("workflow_pack_id") == "feasibility_discovery"

    run_dir = tmp_path / ".product-factory" / "runs" / manifest.run_id
    dossier_path = run_dir / "output" / "FEASIBILITY_DISCOVERY.md"
    assert dossier_path.exists()
    dossier = dossier_path.read_text(encoding="utf-8")
    assert validate_feasibility_document(dossier).status == "pass"
    assert validate_research_provenance(dossier).status == "pass"
    assert validate_option_comparison(dossier).status == "pass"
    assert validate_recommendation(dossier).status == "pass"

    tool_names = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
    assert "create_file" not in tool_names
    assert "apply_patch" not in tool_names
    assert "run_validation_command" not in tool_names


def test_discovery_honors_renamed_deliverable(tmp_path: Path) -> None:
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-discover-rename",
            workflow_type="feasibility_discovery",
            request_text="Assess protocol X.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            pack_input=_pack_input(),
            artifact_overrides={
                "feasibility_dossier": ArtifactOverride(
                    dest_path="docs/payments_feasibility.md",
                )
            },
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    output = tmp_path / ".product-factory" / "runs" / manifest.run_id / "output"
    assert (output / "payments_feasibility.md").exists()
    assert not (output / "FEASIBILITY_DISCOVERY.md").exists()


def test_regulated_discovery_escalates(tmp_path: Path) -> None:
    from product_factory.policy.source_policy import resolve_source_policy

    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-discover-regulated",
            workflow_type="feasibility_discovery",
            request_text="Assess clinical compliance for appointment metadata storage.",
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            pack_input=_pack_input(
                decision_statement="Store clinic appointment metadata in SaaS?",
                domain="clinical compliance",
                source_policy_profile="regulated-domain",
                jurisdiction=None,
            ),
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    dossier = (
        tmp_path
        / ".product-factory"
        / "runs"
        / manifest.run_id
        / "output"
        / "FEASIBILITY_DISCOVERY.md"
    ).read_text(encoding="utf-8")
    assert "needs_expert_review" in dossier.lower()
    assert "expert review:" in dossier.lower()
    policy = resolve_source_policy("regulated-domain", profiles_root=coord.config.root / "profiles")
    assert validate_regulated_claims(dossier, policy=policy).status == "pass"


def test_existing_investigation_pack_still_works(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-invest-still",
            workflow_type="repository_investigation",
            request_text="Map the sample API entry points.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
        )
    )
    assert manifest.final_status == "completed"
    assert (
        tmp_path / ".product-factory" / "runs" / manifest.run_id / "output" / "EVIDENCE_REPORT.md"
    ).exists()


def test_host_submit_discovery_and_rejects_bad_input(tmp_path: Path) -> None:
    import shutil

    project = tmp_path / "project"
    project.mkdir()
    repo_root = Path(__file__).resolve().parents[2]
    shutil.copytree(repo_root / "config", project / "config")
    shutil.copytree(repo_root / "profiles", project / "profiles")
    host = HostService(
        config=load_config(project),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    # Avoid spawning a worker for the reject path.
    rejected = host.submit(
        RunRequest(
            request_id="bad",
            workflow_type="feasibility_discovery",
            request_text="Assess X",
            pack_input={"domain": "payments"},
        ),
        mock=True,
        detach=False,
    )
    assert not rejected.ok
    assert rejected.error is not None
    assert rejected.error.code == "invalid_pack_input"
