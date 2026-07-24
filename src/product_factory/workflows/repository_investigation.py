"""`repository_investigation` workflow pack — read-only evidence reports (P3.D).

Produces a cited evidence report with an assumptions section. No repository
write grants by default; only registered read tools plus artifact write for
the report itself.
"""

from __future__ import annotations

from product_factory.workflows.base import WorkflowPack

REPOSITORY_INVESTIGATION_PACK = WorkflowPack(
    id="repository_investigation",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "request_text": {"type": "string"},
            "repository_path": {"type": ["string", "null"]},
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
            "secret_scan",
            "citation_presence",
        ],
        "review": "optional",
        "behavioral_commands": "none",
        "write_grants": "none",
    },
    skill_policy={"grant_enforcement": "fail_closed"},
    routing_defaults={"coding_worker_tier": "mid"},
    description=(
        "Read-only repository investigation producing an evidence report with "
        "cited paths and an assumptions section — no repository write grants."
    ),
)
