"""Unit tests for change_intake pack + validators (PM2.A)."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.orchestration.coordinator import default_change_intake_plan
from product_factory.planning.compiler import compile_plan
from product_factory.schemas import assert_schema_writable, default_schema_registry
from product_factory.validation.pipeline import (
    request_looks_underspecified,
    validate_intake_no_invention,
    validate_intake_sections,
)
from product_factory.workflows import list_workflow_packs, resolve_workflow_pack
from product_factory.workflows.artifacts import ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST
from product_factory.workflows.change_intake import CHANGE_INTAKE_PACK
from product_factory.workflows.handlers import eligible_next_actions_for_workflow, handler_for
from product_factory.workflows.inputs import validate_pack_input

GOOD_BRIEF = """# CHANGE_BRIEF.md

## Outcome
Add a health endpoint.

## Scope
API surface only.

## Non-goals
- UI changes

## Acceptance criteria
- GET /health returns 200
- Body includes status=ok

## Constraints
- No new dependencies

## Risks
- Route conflicts

## Assumptions
- Existing app factory remains the entry point

## Unknowns
- Exact load-balancer probe path preferences

## Recommended next pack
technical_plan
"""

GOOD_CLARIFICATION = """# CLARIFICATION_REQUEST.md

## Questions
- What concrete outcome should this change produce?
- What is out of scope?

## Blocking unknowns
- Acceptance criteria are not pinned

## Partial outcome
Improve something somehow

## Recommended next pack
none — human clarification required
"""


def test_pack_is_registered() -> None:
    assert "change_intake" in {p.id for p in list_workflow_packs()}
    pack = resolve_workflow_pack("change_intake")
    assert pack is CHANGE_INTAKE_PACK
    roles = {spec.role for spec in pack.artifacts}
    assert roles == {ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST}
    assert all(not spec.required for spec in pack.artifacts)
    assert "implementation" not in pack.allowed_capabilities
    assert "repair" not in pack.allowed_capabilities
    assert "domain_research" not in pack.allowed_capabilities
    assert pack.validation_policy["write_grants"] == "none"


def test_schemas_writable() -> None:
    reg = default_schema_registry()
    assert reg.require("change_brief.v1", for_write=True).reserved is False
    assert reg.require("clarification_request.v1", for_write=True).reserved is False
    assert assert_schema_writable("change_brief.v1") == "change_brief.v1"


def test_handler_plan_compiles_against_pack() -> None:
    scoped = (
        "Add a GET /health endpoint that returns ok. Acceptance criteria: "
        "route registered; existing tests pass. Non-goals: no auth."
    )
    proposal = handler_for("change_intake").plan_template(scoped)
    assert proposal.model_dump() == default_change_intake_plan(scoped).model_dump()
    result = compile_plan(proposal, workflow_pack=CHANGE_INTAKE_PACK)
    assert result.ok, result.errors
    assert proposal.final_artifacts[0].role == ROLE_CHANGE_BRIEF
    for task in proposal.tasks:
        assert task.capability != "implementation"
        assert "file_write" in task.prohibited_actions

    ambiguous = "Please improve something somehow — not sure what."
    clarifying = handler_for("change_intake").plan_template(ambiguous)
    assert clarifying.final_artifacts[0].role == ROLE_CLARIFICATION_REQUEST
    assert compile_plan(clarifying, workflow_pack=CHANGE_INTAKE_PACK).ok


def test_eligible_next_actions_and_feasibility_prefers_intake() -> None:
    actions = eligible_next_actions_for_workflow("change_intake")
    assert {a["pack_id"] for a in actions} >= {"repository_investigation", "technical_plan"}
    discovery = eligible_next_actions_for_workflow("feasibility_discovery")
    assert discovery[0]["pack_id"] == "change_intake"


def test_pack_input_allows_empty_and_rejects_unknown() -> None:
    assert validate_pack_input(CHANGE_INTAKE_PACK, {}) == {}
    assert (
        validate_pack_input(
            CHANGE_INTAKE_PACK,
            {"desired_outcome": "Health check", "known_constraints": ["src/api"]},
        )["desired_outcome"]
        == "Health check"
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_pack_input(CHANGE_INTAKE_PACK, {"request_text": "smuggled"})
    assert "request_text" in exc.value.details["unknown"]


def test_underspecified_heuristic() -> None:
    assert request_looks_underspecified("improve something somehow")
    assert not request_looks_underspecified(
        "Add endpoint with acceptance criteria and non-goals listed explicitly for operators."
    )


def test_intake_validators_pass_and_fail() -> None:
    assert validate_intake_sections(GOOD_BRIEF, role=ROLE_CHANGE_BRIEF).status == "pass"
    assert (
        validate_intake_sections(GOOD_CLARIFICATION, role=ROLE_CLARIFICATION_REQUEST).status
        == "pass"
    )
    incomplete = "# Incomplete\n\n## Outcome\nOnly outcome.\n"
    assert validate_intake_sections(incomplete, role=ROLE_CHANGE_BRIEF).status == "fail"

    assert (
        validate_intake_no_invention(
            GOOD_BRIEF,
            role=ROLE_CHANGE_BRIEF,
            request_text="Add health endpoint with acceptance criteria listed.",
        ).status
        == "pass"
    )
    invented = GOOD_CLARIFICATION + "\n## Acceptance criteria\n- a\n- b\n- c\n- d\n"
    assert (
        validate_intake_no_invention(
            invented,
            role=ROLE_CLARIFICATION_REQUEST,
            request_text="improve something somehow",
        ).status
        == "fail"
    )
    empty_unknowns = GOOD_BRIEF.replace(
        "## Unknowns\n- Exact load-balancer probe path preferences",
        "## Unknowns\n- none",
    )
    assert (
        validate_intake_no_invention(
            empty_unknowns,
            role=ROLE_CHANGE_BRIEF,
            request_text="maybe improve things somehow???",
        ).status
        == "fail"
    )


def test_authority_is_read_only() -> None:
    assert handler_for("change_intake").authority_class() == "read_only"
