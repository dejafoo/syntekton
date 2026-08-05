"""Unit tests for feasibility_discovery pack + validators (PM1.D)."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.orchestration.coordinator import default_feasibility_discovery_plan
from product_factory.planning.compiler import compile_plan
from product_factory.policy.source_policy import (
    SourcePolicyProfile,
    resolve_request_source_policy,
)
from product_factory.validation.pipeline import (
    validate_feasibility_document,
    validate_option_comparison,
    validate_recommendation,
    validate_regulated_claims,
    validate_research_provenance,
)
from product_factory.workflows import list_workflow_packs, resolve_workflow_pack
from product_factory.workflows.artifacts import ROLE_FEASIBILITY_DOSSIER
from product_factory.workflows.feasibility_discovery import FEASIBILITY_DISCOVERY_PACK
from product_factory.workflows.handlers import eligible_next_actions_for_workflow, handler_for
from product_factory.workflows.inputs import validate_pack_input, validate_request_pack_input

REPO_PROFILES = Path(__file__).resolve().parents[2] / "profiles"

GOOD_DOSSIER = """# FEASIBILITY_DISCOVERY.md

## Decision
Should we adopt protocol X?

## Scope
Public docs only.

## Domain model
Vendor boundary.

## Options
- Option A: certified gateway
- Option B: custom adapter

## Comparison rubric
- Capability, interoperability, security/privacy, operational burden, reversibility.
- Option A / Capability: unknown
- Option B / Capability: unknown
- Option A / Reversibility: scored as high
- Option B / Reversibility: scored as medium

## Evidence
- fact: Vendor API documents signed payloads (source_id: src-1, https://example.com/a).
- inference: Retries are likely at-least-once.
- unknown: Exact replay window.

## Assumptions
- No production credentials.

## Unknowns
- Contractual SLA.

## Risks
- Averaging conflicting sources.

## Constraints
- Read-only discovery.

## Recommendation
insufficient_evidence

## Next step
Obtain a current primary source.
"""


def test_pack_is_registered() -> None:
    assert "feasibility_discovery" in {p.id for p in list_workflow_packs()}
    pack = resolve_workflow_pack("feasibility_discovery")
    assert pack is FEASIBILITY_DISCOVERY_PACK
    assert pack.artifacts[0].role == ROLE_FEASIBILITY_DOSSIER
    assert "implementation" not in pack.allowed_capabilities
    assert "repair" not in pack.allowed_capabilities
    assert pack.validation_policy["write_grants"] == "none"
    assert "discovery.evidence-assessment" in pack.skill_policy["allow"]


def test_handler_plan_compiles_against_pack() -> None:
    text = "Assess protocol X feasibility"
    proposal = handler_for("feasibility_discovery").plan_template(text)
    assert proposal.model_dump() == default_feasibility_discovery_plan(text).model_dump()
    result = compile_plan(proposal, workflow_pack=FEASIBILITY_DISCOVERY_PACK)
    assert result.ok, result.errors
    assert any(t.capability == "domain_research" for t in proposal.tasks)
    assert any(t.capability == "decision_analysis" for t in proposal.tasks)
    assert proposal.final_artifacts[0].role == ROLE_FEASIBILITY_DOSSIER
    for task in proposal.tasks:
        assert task.required_tool_classes, task.id
        assert task.capability != "implementation"


def test_eligible_next_actions_point_at_technical_plan() -> None:
    actions = eligible_next_actions_for_workflow("feasibility_discovery")
    assert any(a["pack_id"] == "change_intake" for a in actions)
    assert any(a["pack_id"] == "technical_plan" for a in actions)
    invest = eligible_next_actions_for_workflow("repository_investigation")
    assert any(a["pack_id"] == "feasibility_discovery" for a in invest)


def test_pack_input_requires_decision_and_domain() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(FEASIBILITY_DISCOVERY_PACK, {"domain": "payments"})
    assert exc.value.details["missing"] == ["decision_statement"]


def test_pack_input_rejects_request_text_and_spike() -> None:
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(
            FEASIBILITY_DISCOVERY_PACK,
            {
                "decision_statement": "X?",
                "domain": "payments",
                "request_text": "smuggled",
            },
        )
    assert "request_text" in exc.value.details["unknown"]

    with pytest.raises(ConfigurationError, match="allow_technical_spike"):
        validate_request_pack_input(
            RunRequest(
                request_id="r1",
                workflow_type="feasibility_discovery",
                request_text="Assess X",
                pack_input={
                    "decision_statement": "X?",
                    "domain": "payments",
                    "allow_technical_spike": True,
                },
            )
        )


def test_discovery_defaults_to_public_technical_source_policy() -> None:
    request = RunRequest(
        request_id="r2",
        workflow_type="feasibility_discovery",
        request_text="Assess X",
        pack_input={"decision_statement": "X?", "domain": "payments"},
    )
    policy = resolve_request_source_policy(request, profiles_root=REPO_PROFILES)
    assert policy is not None
    assert policy.id == "public-technical"


def test_feasibility_validators_pass_and_fail() -> None:
    assert validate_feasibility_document(GOOD_DOSSIER).status == "pass"
    assert validate_research_provenance(GOOD_DOSSIER).status == "pass"
    assert validate_option_comparison(GOOD_DOSSIER).status == "pass"
    assert validate_recommendation(GOOD_DOSSIER).status == "pass"
    assert validate_regulated_claims(GOOD_DOSSIER).status == "pass"

    incomplete = "# Incomplete\n\n## Decision\nOnly a decision.\n"
    assert validate_feasibility_document(incomplete).status == "fail"

    bad_fact = GOOD_DOSSIER.replace(
        "- fact: Vendor API documents signed payloads (source_id: src-1, https://example.com/a).",
        "- fact: Vendor API documents signed payloads without citation.",
    )
    assert validate_research_provenance(bad_fact).status == "fail"

    unresolved = validate_research_provenance(
        GOOD_DOSSIER,
        source_records=[{"source_id": "other-id"}],
    )
    assert unresolved.status == "fail"
    assert unresolved.details["unresolved_facts"]


def test_regulated_claims_require_expert_escalation() -> None:
    bad = GOOD_DOSSIER.replace("insufficient_evidence", "feasible").replace(
        "## Evidence",
        "## Evidence\n- fact: Compliance verdict is green (source_id: src-1).\n",
    )
    policy = SourcePolicyProfile(
        id="regulated-domain",
        version="1.0.0",
        require_expert_review_for=["compliance"],
    )
    result = validate_regulated_claims(bad, policy=policy)
    assert result.status == "fail"
    assert "regulated_recommendation_must_escalate" in result.details["problems"]

    good = GOOD_DOSSIER.replace(
        "## Recommendation\ninsufficient_evidence",
        "## Recommendation\nneeds_expert_review\nExpert review: Dr. Ada — required\n",
    ).replace(
        "## Evidence",
        "## Evidence\n- fact: Compliance controls exist (source_id: src-1).\n",
    )
    assert validate_regulated_claims(good, policy=policy).status == "pass"
