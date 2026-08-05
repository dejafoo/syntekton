"""Approval-gated, non-production deployment workflow pack (PM5.B / WF8)."""

from __future__ import annotations

from product_factory.workflows.artifacts import DEPLOYMENT_RECORD_LAND_SPEC, ROLE_DEPLOYMENT_RECORD
from product_factory.workflows.base import WorkflowPack, execution_policy

DEPLOYMENT_EXECUTION_PACK = WorkflowPack(
    id="deployment_execution",
    version="1.0.0",
    input_schema={
        "type": "object",
        "required": [
            "request_text",
            "release_plan",
            "release_plan_digest",
            "artifact_digest",
            "target_id",
            "change_window",
            "idempotency_key",
        ],
        "properties": {
            "request_text": {"type": "string"},
            "release_plan": {
                "type": "object",
                "required": ["outcome"],
                "properties": {"outcome": {"const": "ready"}},
            },
            "release_plan_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "artifact_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "target_id": {"type": "string", "minLength": 1},
            "change_window": {"type": "object"},
            "idempotency_key": {"type": "string", "minLength": 1},
            "approval_binding": {
                "type": "object",
                "required": [
                    "approval_id",
                    "release_plan_digest",
                    "artifact_digest",
                    "target_id",
                    "change_window",
                ],
            },
            "action_log": {"type": "array"},
            "health_checks": {"type": "array"},
            "observed_metrics": {"type": "array"},
            "rollback_result": {"type": ["object", "null"]},
        },
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "required": ["deployment_record"],
        "properties": {"deployment_record": {"type": "object"}},
    },
    allowed_capabilities=frozenset({"deployment_execution", "composition"}),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["deployment_record_contract", "secret_scan"],
        "approval_required": True,
        "production": "prohibited",
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": ["deployment.change-control"],
    },
    routing_defaults={"deployment_execution_profile": "supervisor"},
    execution_policy=execution_policy(
        capabilities=frozenset({"deployment_execution", "composition"}),
        validators=["deployment_record_contract", "secret_scan"],
        output_roles=(ROLE_DEPLOYMENT_RECORD,),
        allowed_tool_classes=frozenset({"deployment_read", "deployment_write", "artifact_write"}),
        denied_tool_names=frozenset(
            {"create_file", "apply_patch", "web_search", "query_service_signals"}
        ),
        fallback_composition_roles=frozenset({ROLE_DEPLOYMENT_RECORD}),
        required_output_roles=frozenset({ROLE_DEPLOYMENT_RECORD}),
        accepted_handoff_schemas=frozenset({"release_plan.v1"}),
        accepted_handoff_states=frozenset({"approved"}),
        accepted_handoff_roles={"release_plan.v1": frozenset({"release_plan"})},
        approval_required=True,
        evaluation_fixture_id="deployment_execution.v1",
    ),
    artifacts=(DEPLOYMENT_RECORD_LAND_SPEC,),
    description=(
        "Approval-gated deployment of one immutable artifact to an allowlisted "
        "non-production target with health, halt, rollback, and reconciliation receipts."
    ),
)

__all__ = ["DEPLOYMENT_EXECUTION_PACK"]
