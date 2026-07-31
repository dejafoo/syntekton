"""`feasibility_discovery` workflow pack — bounded public-evidence discovery (PM1.D / WF1).

Produces a source-grounded feasibility dossier. Read-only: no repository write
grants, no technical spike, and never implementation/repair capabilities.
"""

from __future__ import annotations

from product_factory.workflows.artifacts import ROLE_FEASIBILITY_DOSSIER, ArtifactLandSpec
from product_factory.workflows.base import WorkflowPack

FEASIBILITY_DISCOVERY_PACK = WorkflowPack(
    id="feasibility_discovery",
    version="1.0.0",
    input_schema={
        "type": "object",
        "properties": {
            "decision_statement": {"type": "string"},
            "domain": {"type": "string"},
            "jurisdiction": {"type": ["string", "null"]},
            "actors": {"type": "array", "items": {"type": "string"}},
            "deployment_context": {"type": ["string", "null"]},
            "allowed_source_classes": {
                "type": "array",
                "items": {"type": "string"},
            },
            "source_freshness_days": {"type": ["integer", "null"]},
            "source_policy_profile": {"type": ["string", "null"]},
            "seed_source_urls": {
                "type": "array",
                "items": {"type": "string"},
            },
            "research_budget": {"type": ["object", "null"]},
            # PM1 refuses technical spikes; callers must leave this false/absent.
            "allow_technical_spike": {"type": "boolean"},
        },
        "required": ["decision_statement", "domain"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "feasibility_dossier": {"type": "string"},
            "validation_results": {"type": "array"},
        },
    },
    allowed_capabilities=frozenset(
        {
            "domain_research",
            "decision_analysis",
            "requirements",
            "repository_analysis",
            "independent_review",
            "documentation",
            "composition",
        }
    ),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": [
            "feasibility_sections",
            "research_provenance",
            "option_comparison",
            "regulated_claims_review",
            "secret_scan",
        ],
        "review": "required",
        "behavioral_commands": "none",
        "write_grants": "none",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": [
            "discovery.evidence-assessment",
            "discovery.option-framing",
            "repository-inspection",
        ],
    },
    routing_defaults={"coding_worker_tier": "mid"},
    artifacts=(
        ArtifactLandSpec(
            role=ROLE_FEASIBILITY_DOSSIER,
            default_logical_name="FEASIBILITY_DISCOVERY.md",
            default_dest_path="docs/FEASIBILITY_DISCOVERY.md",
            description="Source-grounded feasibility dossier for a bounded discovery question.",
        ),
    ),
    description=(
        "Bounded public-evidence discovery producing a feasibility dossier with "
        "labeled provenance, option comparison, and an expert-review escalations path."
    ),
)
