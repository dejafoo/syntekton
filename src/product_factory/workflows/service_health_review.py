"""Read-only service-health-review workflow pack (PM5.D / WF9)."""

from product_factory.workflows.artifacts import (
    OPERATIONAL_RECORD_LAND_SPEC,
    ROLE_OPERATIONAL_RECORD,
)
from product_factory.workflows.base import WorkflowPack, execution_policy
from product_factory.workflows.incident_triage import _DENIED_OPERATIONAL_MUTATIONS

SERVICE_HEALTH_REVIEW_PACK = WorkflowPack(
    id="service_health_review",
    version="1.0.0",
    input_schema={
        "type": "object",
        "required": ["request_text", "service_id", "environment", "start", "end"],
        "properties": {
            "request_text": {"type": "string"},
            "service_id": {"type": "string", "minLength": 1},
            "environment": {"type": "string", "minLength": 1},
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
            "evidence": {"type": "array"},
            "observations": {"type": "array"},
            "hypotheses": {"type": "array"},
            "timeline": {"type": "array"},
            "recommendations": {"type": "array"},
            "query_hashes": {"type": "array"},
            "slo_breach": {"type": "boolean"},
            "follow_up": {
                "type": "string",
                "enum": [
                    "change_intake",
                    "repository_investigation",
                    "rollback_decision",
                    "human_escalation",
                    "none",
                ],
            },
        },
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "required": ["operational_record"],
        "properties": {"operational_record": {"type": "object"}},
    },
    allowed_capabilities=frozenset({"operations_analysis", "composition"}),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["operational_record_contract", "secret_scan"],
        "write_grants": "none",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": ["operations.incident-synthesis"],
    },
    routing_defaults={"operations_analysis_profile": "fast_worker"},
    execution_policy=execution_policy(
        capabilities=frozenset({"operations_analysis", "composition"}),
        validators=["operational_record_contract", "secret_scan"],
        output_roles=(ROLE_OPERATIONAL_RECORD,),
        denied_tool_names=_DENIED_OPERATIONAL_MUTATIONS,
        fallback_composition_roles=frozenset({ROLE_OPERATIONAL_RECORD}),
        required_output_roles=frozenset({ROLE_OPERATIONAL_RECORD}),
        evaluation_fixture_id="service_health_review.v1",
    ),
    artifacts=(OPERATIONAL_RECORD_LAND_SPEC,),
    description=(
        "Read-only review of bounded service-health signals with explicit "
        "observation/inference labels and typed follow-up."
    ),
)

__all__ = ["SERVICE_HEALTH_REVIEW_PACK"]
