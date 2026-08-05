"""`change_intake` workflow pack — frame a change request into a brief or clarification (PM2.A / WF2).

Produces exactly one primary landable: a pinned `change_brief` or a typed
`clarification_request`. Read-only: no repository write grants, no
implementation/repair, and no new live research plane (discovery already did that).
"""

from __future__ import annotations

from product_factory.workflows.artifacts import (
    ROLE_CHANGE_BRIEF,
    ROLE_CLARIFICATION_REQUEST,
    ArtifactLandSpec,
)
from product_factory.workflows.base import WorkflowPack, execution_policy

# Required headings for a landable change brief (content-keyed, not basename).
CHANGE_BRIEF_REQUIRED_SECTIONS = (
    "Outcome",
    "Scope",
    "Non-goals",
    "Acceptance criteria",
    "Constraints",
    "Risks",
    "Assumptions",
    "Unknowns",
    "Recommended next pack",
)

CLARIFICATION_REQUIRED_SECTIONS = (
    "Questions",
    "Blocking unknowns",
    "Partial outcome",
)

CHANGE_INTAKE_VALIDATOR_IDS: dict[str, str] = {
    ROLE_CHANGE_BRIEF: "intake_sections",
    ROLE_CLARIFICATION_REQUEST: "intake_sections",
}

CHANGE_INTAKE_PACK = WorkflowPack(
    id="change_intake",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "decision_statement": {"type": "string"},
            "desired_outcome": {"type": "string"},
            "known_constraints": {
                "type": "array",
                "items": {"type": "string"},
            },
            # Optional pin to a prior discovery run; dossier content arrives via
            # handoff_refs on the request envelope, not here.
            "source_run_id": {"type": ["string", "null"]},
        },
        # request_text lives on the envelope (ENVELOPE_KEYS); empty pack_input is OK.
        "required": [],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "change_brief": {"type": ["string", "null"]},
            "clarification_request": {"type": ["string", "null"]},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(
        {
            "requirements",
            "repository_analysis",
            "documentation",
            "composition",
            "independent_review",
            "decision_analysis",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "intake_sections",
            "intake_no_invention",
            "secret_scan",
        ],
        "review": "required",
        "behavioral_commands": "none",
        "write_grants": "none",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": [
            "repository-inspection",
        ],
    },
    routing_defaults={"coding_worker_tier": "mid"},
    execution_policy=execution_policy(
        capabilities=frozenset(
            {
                "requirements",
                "repository_analysis",
                "documentation",
                "composition",
                "independent_review",
                "decision_analysis",
            }
        ),
        validators=["intake_sections", "intake_no_invention", "secret_scan"],
        output_roles=(ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST),
        denied_tool_names=frozenset(
            {
                "create_file",
                "apply_patch",
                "run_validation_command",
                "web_search",
                "fetch_source",
            }
        ),
        fallback_composition_roles=frozenset({ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST}),
        exactly_one_output_role_groups=(
            frozenset({ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST}),
        ),
        evaluation_fixture_id="change_intake.v1",
    ),
    artifacts=(
        # Exactly one of these is produced as the primary landable; both are
        # optional in the land map so the coordinator can accept either outcome.
        ArtifactLandSpec(
            role=ROLE_CHANGE_BRIEF,
            default_logical_name="CHANGE_BRIEF.md",
            default_dest_path="docs/CHANGE_BRIEF.md",
            required=False,
            description="Pinned change brief for a well-scoped request.",
        ),
        ArtifactLandSpec(
            role=ROLE_CLARIFICATION_REQUEST,
            default_logical_name="CLARIFICATION_REQUEST.md",
            default_dest_path="docs/CLARIFICATION_REQUEST.md",
            required=False,
            description="Typed clarification questions when the request is under-specified.",
        ),
    ),
    description=(
        "Frame a vague or well-scoped change request into either a pinned "
        "change brief or a typed clarification request. Read-only; never opens "
        "a new live research plane or grants repository writes."
    ),
)
