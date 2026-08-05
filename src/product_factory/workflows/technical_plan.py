"""`technical_plan` workflow pack — acceptance-linked implementation planning.

Requirements, architecture decision, acceptance criteria, and handoff tasks
match the prior architecture coordinator path. `architecture` remains a
one-release alias resolved by `workflows/registry.py` (P3.D).
"""

from __future__ import annotations

from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ArtifactLandSpec,
)
from product_factory.workflows.base import WorkflowPack, execution_policy

TECHNICAL_PLAN_PACK = WorkflowPack(
    id="technical_plan",
    version="2.0.0",
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
            "interface_analysis",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "architecture_sections",
            "architecture_substance",
            "acceptance_verification_links",
            "no_invented_defaults",
            "secret_scan",
        ],
        "accepted_handoff_schemas": [
            "change_brief.v1",
            "evidence_report.document.v1",
            "evidence_report.document.v2",
            "feasibility_dossier.v1",
        ],
        "required_handoff_schema": "change_brief.v1",
        "review": "optional",
        "behavioral_commands": "none",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": [
            "architecture.system-design",
            "integration.contract-analysis",
            "integration.technical-spike",
        ],
    },
    routing_defaults={"coding_worker_tier": "mid"},
    execution_policy=execution_policy(
        capabilities=frozenset(
            {
                "requirements",
                "architecture",
                "composition",
                "independent_review",
                "documentation",
                "interface_analysis",
            }
        ),
        validators=[
            "architecture_sections",
            "architecture_substance",
            "acceptance_verification_links",
            "no_invented_defaults",
            "secret_scan",
        ],
        output_roles=(ROLE_ARCHITECTURE_DOCUMENT,),
        required_output_roles=frozenset({ROLE_ARCHITECTURE_DOCUMENT}),
        fallback_composition_roles=frozenset({ROLE_ARCHITECTURE_DOCUMENT}),
        accepted_handoff_schemas=frozenset(
            {
                "change_brief.v1",
                "evidence_report.document.v1",
                "evidence_report.document.v2",
                "feasibility_dossier.v1",
            }
        ),
        evaluation_fixture_id="technical_plan.v2",
    ),
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
        "Technical plan from pinned ChangeBrief/evidence inputs, mapping every "
        "acceptance criterion to an implementation slice and verification evidence."
    ),
)
