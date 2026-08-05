"""Unit tests for digest-stable domain reference packs (PM5.C / G4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.policy.domain_packs import (
    DomainPackRegistry,
    DomainReferencePack,
    resolve_domain_reference_pack,
    resolve_request_domain_packs,
)
from product_factory.policy.policy_profiles import (
    PolicyProfileRegistry,
    resolve_policy_profile,
)

ROOT = Path(__file__).resolve().parents[2]
PACKS_ROOT = ROOT / "packs"
PROFILES_ROOT = ROOT / "profiles"


def test_fhir_domain_pack_loads_with_stable_digest() -> None:
    first = resolve_domain_reference_pack("fhir-r4-public", packs_root=PACKS_ROOT)
    second = DomainPackRegistry.load(PACKS_ROOT).require("fhir-r4-public")
    assert first.id == "fhir-r4-public"
    assert first.version == "1.0.0"
    assert first.domain == "health-interoperability"
    assert first.data_classification == "synthetic"
    assert first.required_review == "domain_expert"
    assert first.grants.additional_tool_classes == []
    assert first.grants.additional_authority == []
    assert first.digest == second.digest
    assert len(first.digest) == 64
    assert first.as_manifest_entry() == {f"domain_pack:{first.id}": first.digest}
    assert "Patient resource" in yaml.safe_dump(first.content)


def test_composition_policy_profiles_are_digest_stable() -> None:
    regulated = resolve_policy_profile("regulated-data", profiles_root=PROFILES_ROOT)
    deploy = resolve_policy_profile("deployment-composition", profiles_root=PROFILES_ROOT)
    again = PolicyProfileRegistry.load(PROFILES_ROOT)
    assert regulated.digest == again.require("regulated-data").digest
    assert deploy.digest == again.require("deployment-composition").digest
    assert regulated.deny_authority_widening is True
    assert deploy.require_approval_for_effect is True
    assert deploy.provider_target_profiles_require_broker is True


def test_unknown_domain_pack_fails_closed() -> None:
    with pytest.raises(ConfigurationError, match="Unknown domain reference pack"):
        resolve_domain_reference_pack("missing-pack", packs_root=PACKS_ROOT)


def test_request_resolves_named_domain_pack() -> None:
    request = RunRequest(
        request_id="req-domain",
        workflow_type="feasibility_discovery",
        request_text="FHIR façade",
        pack_input={
            "decision_statement": "Use FHIR R4?",
            "domain": "health-interoperability",
            "domain_reference_pack": "fhir-r4-public",
        },
    )
    packs = resolve_request_domain_packs(request, packs_root=PACKS_ROOT)
    assert [pack.id for pack in packs] == ["fhir-r4-public"]


def test_domain_pack_rejects_authority_claims() -> None:
    pack = DomainReferencePack(
        id="hostile",
        grants={"additional_tool_classes": ["deployment_write"], "additional_authority": []},
    )
    with pytest.raises(ConfigurationError, match="must not declare additional authority"):
        pack.asserts_no_authority()
