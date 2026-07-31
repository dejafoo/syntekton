"""`technical_plan` workflow pack — wraps the historical `architecture` behavior.

Requirements, architecture decision, acceptance criteria, and handoff tasks
match the prior architecture coordinator path. `architecture` remains a
one-release alias resolved by `workflows/registry.py` (P3.D).
"""

from __future__ import annotations

from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ArtifactLandSpec,
)
from product_factory.workflows.base import WorkflowPack

TECHNICAL_PLAN_PACK = WorkflowPack(
    id="technical_plan",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string"},
            "repository_path": {"type": ["string", "null"]},
            "must_cover": {"type": "string"},
        },
        "required": ["request_text"],
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "architecture_document": {"type": "string"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(
        {
            "requirements",
            "architecture",
            "composition",
            "independent_review",
            "documentation",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "architecture_sections",
            "architecture_substance",
            "secret_scan",
        ],
        "review": "optional",
        "behavioral_commands": "none",
    },
    skill_policy={"grant_enforcement": "fail_closed"},
    routing_defaults={"coding_worker_tier": "mid"},
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_ARCHITECTURE_DOCUMENT,
            default_logical_name="ARCHITECTURE.md",
            default_dest_path="docs/ARCHITECTURE.md",
            description=(
                "Technical plan / architecture document. Override the name for "
                "scoped plans, e.g. docs/integration_testing_architecture.md."
            ),
        ),
    ),
    description=(
        "Technical plan with requirements, architecture decision, acceptance "
        "criteria, and implementation handoff — behavior-frozen wrapper around "
        "the historical architecture workflow."
    ),
)
