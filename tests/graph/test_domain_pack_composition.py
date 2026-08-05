"""Graph composition for domain/policy packs (PM5.C / G4)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import yaml

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.policy.composition_gates import evaluate_composition_gates
from product_factory.policy.domain_packs import DomainReferencePack
from product_factory.policy.policy_profiles import resolve_request_policy_profiles

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "domain" / "fhir_style_discovery.yaml"


def _coord(tmp_path: Path) -> RunCoordinator:
    return RunCoordinator(
        config=load_config(ROOT),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_fhir_domain_composition_escalates_and_stays_read_only(tmp_path: Path) -> None:
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-pm5c-fhir",
            workflow_type="feasibility_discovery",
            request_text=str(fixture["decision_statement"]),
            budget=RunBudget(max_cost_usd=Decimal("2.00")),
            approval_policy="none",
            pack_input={
                "decision_statement": fixture["decision_statement"],
                "domain": fixture["domain"],
                "jurisdiction": fixture.get("jurisdiction"),
                "source_policy_profile": fixture["source_policy_profile"],
                "domain_reference_pack": fixture["domain_reference_pack"],
                "composition_policy_profile": fixture["composition_policy_profile"],
                "allow_technical_spike": False,
            },
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
    assert "needs_expert_review" in dossier
    assert "compliant" not in dossier.lower()
    tool_names = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
    assert "create_file" not in tool_names
    assert "apply_patch" not in tool_names
    assert "start_deployment" not in tool_names
    assert "rollback_deployment" not in tool_names

    tasks = coord.db.list_tasks(manifest.run_id)
    policies = [
        json.loads(row["effective_policy_json"])
        for row in tasks
        if row.get("effective_policy_json")
    ]
    assert policies
    assert any("fhir-r4-public" in (p.get("reference_pack_ids") or []) for p in policies)
    for policy in policies:
        granted = set(policy.get("allowed_tool_names") or [])
        assert not granted & {
            "start_deployment",
            "rollback_deployment",
            "create_file",
            "apply_patch",
        }


def test_extra_connector_grant_via_domain_pack_fails_closed() -> None:
    """Hostile domain pack authority claims surface as composition_conflict."""
    request = RunRequest(
        request_id="req-pm5c-conflict",
        workflow_type="feasibility_discovery",
        request_text="Assess FHIR façade",
        pack_input={
            "decision_statement": "Assess FHIR façade",
            "domain": "health-interoperability",
            "source_policy_profile": "regulated-domain",
            "composition_policy_profile": "regulated-data",
            "allow_technical_spike": False,
        },
    )
    profiles = resolve_request_policy_profiles(request, profiles_root=ROOT / "profiles")
    hostile = DomainReferencePack(
        id="hostile-widening",
        permitted_workflows=["feasibility_discovery"],
        grants={
            "additional_tool_classes": ["deployment_write"],
            "additional_authority": ["external_write"],
        },
    )
    result = evaluate_composition_gates(
        request=request,
        domain_packs=[hostile],
        policy_profiles=profiles,
        granted_tool_names={"write_artifact", "start_deployment"},
        granted_tool_classes={"artifact_write", "deployment_write"},
        skill_ids=["deployment.change-control"],
    )
    assert not result.ok
    assert result.summary == "composition_conflict"
    kinds = {item["kind"] for item in result.conflicts}
    assert "domain_pack_authority" in kinds
    assert "authority_widening" in kinds or "skill_authority_smuggle" in kinds
