"""`repository_change` workflow pack — wraps the historical `code_change` behavior.

Same fixed plan shapes, optional review, repair, and composition as before;
this pack only makes that contract explicit and versioned. `code_change`
remains a one-release alias resolved by `workflows/registry.py`.
"""

from __future__ import annotations

from product_factory.domain.capabilities import CAPABILITIES
from product_factory.workflows.artifacts import ROLE_PROPOSED_PATCH, ArtifactLandSpec
from product_factory.workflows.base import WorkflowPack

REPOSITORY_CHANGE_PACK = WorkflowPack(
    id="repository_change",
    version="1.0.0",
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
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(CAPABILITIES),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["patch_applies", "path_scope", "secret_scan"],
        "review": "optional",
        "behavioral_commands": "registered_only",
    },
    skill_policy={"grant_enforcement": "fail_closed"},
    routing_defaults={"coding_worker_tier": "mid"},
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
    ),
    description=(
        "Bounded repository change with fixed plan, optional review, repair, and "
        "composition — behavior-frozen wrapper around the historical code_change workflow."
    ),
)
