"""Composition gates for regulated discovery and deployment (PM5.C)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from product_factory.domain.runs import RunRequest
from product_factory.policy.composition_gates import (
    CompositionConflictError,
    assert_no_authority_widening,
    evaluate_composition_gates,
)
from product_factory.policy.domain_packs import (
    DomainReferencePack,
    resolve_request_domain_packs,
)
from product_factory.policy.policy_profiles import resolve_request_policy_profiles
from product_factory.skills.registry import SkillRegistry
from product_factory.workflows.registry import resolve_workflow_pack

ROOT = Path(__file__).resolve().parents[2]
PACKS_ROOT = ROOT / "packs"
PROFILES_ROOT = ROOT / "profiles"
FIXTURE = ROOT / "tests" / "fixtures" / "domain" / "fhir_style_discovery.yaml"


def _fhir_request(**updates: object) -> RunRequest:
    fixture = yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))
    pack_input = {
        "decision_statement": fixture["decision_statement"],
        "domain": fixture["domain"],
        "jurisdiction": fixture.get("jurisdiction"),
        "source_policy_profile": fixture["source_policy_profile"],
        "domain_reference_pack": fixture["domain_reference_pack"],
        "composition_policy_profile": fixture["composition_policy_profile"],
        "allow_technical_spike": False,
    }
    pack_input.update(updates)
    return RunRequest(
        request_id="req-pm5c-gates",
        workflow_type="feasibility_discovery",
        request_text=str(fixture["decision_statement"]),
        pack_input=pack_input,
    )


def test_fhir_fixture_requires_human_review_without_mutation_authority() -> None:
    request = _fhir_request()
    packs = resolve_request_domain_packs(request, packs_root=PACKS_ROOT)
    profiles = resolve_request_policy_profiles(request, profiles_root=PROFILES_ROOT)
    result = evaluate_composition_gates(
        request=request,
        domain_packs=packs,
        policy_profiles=profiles,
        granted_tool_names={"write_artifact", "web_search", "fetch_source"},
        granted_tool_classes={"artifact_write", "web_read"},
        skill_ids=["discovery.evidence-assessment", "discovery.option-framing"],
    )
    assert result.ok
    assert result.requires_human_review
    assert result.recommendation == "needs_expert_review"
    assert result.reference_pack_ids == ["fhir-r4-public"]
    assert "regulated-data" in result.policy_profile_ids
    assert any(key.startswith("domain_pack:") for key in result.profile_digests)
    assert any(key.startswith("composition_policy:") for key in result.profile_digests)


def test_method_profile_tool_separation_for_discovery_and_deploy() -> None:
    discovery = resolve_workflow_pack("feasibility_discovery")
    deploy = resolve_workflow_pack("deployment_execution")
    assert "deployment_execution" not in discovery.allowed_capabilities
    assert deploy.execution_policy.approval_required is True

    skills = SkillRegistry.load(ROOT / "skills")
    discovery_skills = skills.match(
        capability="domain_research",
        skill_policy=dict(discovery.skill_policy),
    )
    assert all(s.manifest.id != "deployment.change-control" for s in discovery_skills)
    assert all("deployment_write" not in s.manifest.required_tools for s in discovery_skills)

    deploy_skills = skills.match(
        capability="deployment_execution",
        skill_policy=dict(deploy.skill_policy),
    )
    assert [s.manifest.id for s in deploy_skills] == ["deployment.change-control"]


def test_skill_selection_cannot_widen_read_only_mutation_authority() -> None:
    request = _fhir_request()
    packs = resolve_request_domain_packs(request, packs_root=PACKS_ROOT)
    profiles = resolve_request_policy_profiles(request, profiles_root=PROFILES_ROOT)
    result = evaluate_composition_gates(
        request=request,
        domain_packs=packs,
        policy_profiles=profiles,
        granted_tool_names={"write_artifact", "start_deployment"},
        granted_tool_classes={"artifact_write", "deployment_write"},
        skill_ids=["discovery.evidence-assessment", "deployment.change-control"],
    )
    assert not result.ok
    assert result.summary == "composition_conflict"
    kinds = {item["kind"] for item in result.conflicts}
    assert "authority_widening" in kinds or "skill_authority_smuggle" in kinds


def test_domain_pack_authority_claims_fail_closed() -> None:
    hostile = DomainReferencePack(
        id="hostile-fhir",
        permitted_workflows=["feasibility_discovery"],
        grants={
            "additional_tool_classes": ["deployment_write"],
            "additional_authority": ["external_write"],
        },
    )
    with pytest.raises(CompositionConflictError, match="composition_conflict"):
        assert_no_authority_widening(
            workflow_type="feasibility_discovery",
            granted_tool_names={"write_artifact"},
            granted_tool_classes={"artifact_write"},
            domain_packs=[hostile],
            policy_profiles=resolve_request_policy_profiles(
                _fhir_request(), profiles_root=PROFILES_ROOT
            ),
        )


def test_deployment_composition_policy_defaults_for_deploy_pack() -> None:
    request = RunRequest(
        request_id="req-deploy-compose",
        workflow_type="deployment_execution",
        request_text="Deploy approved staging release",
        pack_input={
            "request_text": "Deploy approved staging release",
            "release_plan": {"outcome": "ready"},
            "release_plan_digest": "a" * 64,
            "artifact_digest": "b" * 64,
            "target_id": "staging-local",
            "change_window": {
                "start": "2026-08-01T00:00:00Z",
                "end": "2026-08-01T01:00:00Z",
            },
            "idempotency_key": "deploy-1",
        },
    )
    profiles = resolve_request_policy_profiles(request, profiles_root=PROFILES_ROOT)
    assert [profile.id for profile in profiles] == ["deployment-composition"]
    result = evaluate_composition_gates(
        request=request,
        domain_packs=[],
        policy_profiles=profiles,
        granted_tool_names={"resolve_deployment_target", "start_deployment"},
        granted_tool_classes={"deployment_read", "deployment_write"},
        skill_ids=["deployment.change-control"],
    )
    assert result.ok
    assert result.policy_profile_ids == ["deployment-composition"]
