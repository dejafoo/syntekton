"""Security tests for domain/policy pack authority boundaries (PM5.C)."""

from __future__ import annotations

from pathlib import Path

from product_factory.policy.domain_packs import DomainPackRegistry
from product_factory.policy.policy_profiles import PolicyProfileRegistry
from product_factory.skills.registry import SkillRegistry
from product_factory.workflows.registry import list_workflow_packs

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_MARKERS = (
    "api_key",
    "password=",
    "bearer ",
    "private_key",
    "ehr://",
    "prod.internal",
    "production.endpoint",
)


def test_domain_packs_never_grant_mutation_or_credentials() -> None:
    registry = DomainPackRegistry.load(ROOT / "packs")
    assert "fhir-r4-public" in registry.ids()
    for pack_id in registry.ids():
        pack = registry.require(pack_id)
        assert pack.grants.additional_tool_classes == []
        assert pack.grants.additional_authority == []
        blob = (pack.model_dump_json() + str(pack.content)).lower()
        for marker in FORBIDDEN_MARKERS:
            assert marker not in blob, (pack_id, marker)
        for claim in pack.content.get("prohibited_claims") or []:
            assert (
                "compliance" in claim.lower()
                or "clinical" in claim.lower()
                or "fhir" in claim.lower()
            )


def test_skills_contain_no_ehr_or_production_credentials() -> None:
    skills = SkillRegistry.load(ROOT / "skills")
    for skill in skills.skills:
        blob = (skill.content + skill.manifest.model_dump_json()).lower()
        for marker in ("ehr://", "prod.internal", "production.endpoint", "api_key="):
            assert marker not in blob, skill.manifest.id
        assert "deployment_write" not in skill.manifest.required_tools
        if skill.manifest.id == "deployment.change-control":
            assert skill.manifest.capabilities == ["deployment_execution"]


def test_composition_policies_forbid_compliance_verdicts() -> None:
    profiles = PolicyProfileRegistry.load(ROOT / "profiles")
    regulated = profiles.require("regulated-data")
    assert "compliance_verdict" in regulated.prohibited_conclusions
    assert "clinical_safety_verdict" in regulated.prohibited_conclusions
    deploy = profiles.require("deployment-composition")
    assert deploy.provider_target_profiles_require_broker is True


def test_domain_reference_packs_are_not_workflow_packs() -> None:
    workflow_ids = {pack.id for pack in list_workflow_packs()}
    domain_ids = set(DomainPackRegistry.load(ROOT / "packs").ids())
    assert domain_ids.isdisjoint(workflow_ids)
    assert "fhir-r4-public" not in workflow_ids
