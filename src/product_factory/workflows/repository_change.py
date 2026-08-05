"""`repository_change` workflow pack — wraps the historical `code_change` behavior.

Same fixed plan shapes, optional review, repair, and composition as before;
this pack only makes that contract explicit and versioned. `code_change`
remains a one-release alias resolved by `workflows/registry.py`.
"""

from __future__ import annotations

from product_factory.domain.capabilities import CAPABILITIES
from product_factory.workflows.artifacts import (
    ROLE_CHANGE_SET,
    ROLE_PROPOSED_PATCH,
    ArtifactLandSpec,
)
from product_factory.workflows.base import WorkflowPack, execution_policy

REPOSITORY_CHANGE_PACK = WorkflowPack(
    id="repository_change",
    version="2.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string"},
            "repository_path": {"type": ["string", "null"]},
            "validation_commands": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["request_text"],
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "proposed_patch": {"type": "string"},
            "change_set": {"type": "object"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(CAPABILITIES),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["patch_applies", "path_scope", "secret_scan"],
        "accepted_handoff_schemas": [
            "technical_plan.document.v1",
            "technical_plan.document.v2",
            "change_brief.v1",
        ],
        "accepted_handoff_roles": {
            "technical_plan.document.v1": ["architecture_document"],
            "technical_plan.document.v2": ["architecture_document"],
            "change_brief.v1": ["change_brief"],
        },
        "accepted_handoff_states": ["approved"],
        "review": "optional",
        "behavioral_commands": "registered_only",
    },
    skill_policy={"grant_enforcement": "fail_closed"},
    routing_defaults={"coding_worker_tier": "mid"},
    execution_policy=execution_policy(
        capabilities=frozenset(CAPABILITIES),
        validators=["patch_applies", "path_scope", "secret_scan"],
        output_roles=(ROLE_PROPOSED_PATCH, ROLE_CHANGE_SET),
        accepted_handoff_schemas=frozenset(
            {
                "technical_plan.document.v1",
                "technical_plan.document.v2",
                "change_brief.v1",
            }
        ),
        accepted_handoff_states=frozenset({"approved"}),
        accepted_handoff_roles={
            "technical_plan.document.v1": frozenset({"architecture_document"}),
            "technical_plan.document.v2": frozenset({"architecture_document"}),
            "change_brief.v1": frozenset({"change_brief"}),
        },
        repair_eligible_capabilities=frozenset(
            {"implementation", "repair", "composition", "independent_review"}
        ),
        approval_required=True,
        evaluation_fixture_id="repository_change.v2",
    ),
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_PROPOSED_PATCH,
            default_logical_name="proposed.patch",
            default_dest_path="proposed.patch",
            media_type="text/x-diff",
            # Patches reach the repository through `approve --apply`, never by copy,
            # and `apply_patch` reads the file back by path, so the name is fixed.
            landable=False,
            renamable=False,
        ),
        ArtifactLandSpec(
            role=ROLE_CHANGE_SET,
            default_logical_name="change-set.json",
            default_dest_path="change-set.json",
            media_type="application/json",
            landable=False,
            renamable=False,
            description="Content-addressed summary of the proposed repository change.",
        ),
    ),
    description=(
        "Bounded repository change producing a patch and content-addressed ChangeSet, "
        "with optional review and repair before composition."
    ),
)
