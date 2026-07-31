"""`repository_investigation` workflow pack — pinned read-only evidence reports.

Produces a cited evidence report with an assumptions section. No repository
write grants by default; only registered read tools plus artifact write for
the report itself.
"""

from __future__ import annotations

from product_factory.workflows.artifacts import ROLE_EVIDENCE_REPORT, ArtifactLandSpec
from product_factory.workflows.base import WorkflowPack

REPOSITORY_INVESTIGATION_PACK = WorkflowPack(
    id="repository_investigation",
    version="2.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string"},
            "repository_path": {"type": ["string", "null"]},
            "repository_revision": {"type": ["string", "null"]},
            "retrieval_started_at": {"type": ["string", "null"]},
            "retrieval_ended_at": {"type": ["string", "null"]},
        },
        "required": ["request_text"],
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "properties": {
            "evidence_report": {"type": "string"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(
        {
            "repository_analysis",
            "independent_review",
            "documentation",
            "composition",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "investigation_sections",
            "investigation_provenance",
            "secret_scan",
            "citation_presence",
        ],
        "accepted_handoff_schemas": [
            "change_brief.v1",
            "feasibility_dossier.v1",
        ],
        "required_handoff_schema": "change_brief.v1",
        "review": "optional",
        "behavioral_commands": "none",
        "write_grants": "none",
    },
    skill_policy={"grant_enforcement": "fail_closed"},
    routing_defaults={"coding_worker_tier": "mid"},
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_EVIDENCE_REPORT,
            default_logical_name="EVIDENCE_REPORT.md",
            default_dest_path="docs/EVIDENCE_REPORT.md",
            description="Cited evidence report for a read-only investigation.",
        ),
    ),
    description=(
        "Read-only repository investigation consuming a pinned ChangeBrief and "
        "producing labeled facts, inferences, unknowns, and source provenance."
    ),
)
