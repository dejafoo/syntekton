"""Monitor-only release-readiness workflow pack (PM5.A / WF7)."""

from __future__ import annotations

from product_factory.workflows.artifacts import RELEASE_PLAN_LAND_SPEC, ROLE_RELEASE_PLAN
from product_factory.workflows.base import WorkflowPack, execution_policy

RELEASE_READINESS_PACK = WorkflowPack(
    id="release_readiness",
    version="1.0.0",
    input_schema={
        "type": "object",
        "required": ["request_text"],
        "properties": {
            "request_text": {"type": "string"},
            "repository": {"type": "string"},
            "commit_sha": {"type": "string"},
            "version": {"type": "string"},
            "input_digests": {
                "type": "object",
                "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            },
            "verification_evidence": {"type": "array"},
            "migration_preconditions": {"type": "array"},
            "rollback_criteria": {"type": "array"},
            "unresolved_decisions": {"type": "array"},
        },
        "additionalProperties": True,
    },
    output_schema={
        "type": "object",
        "required": ["release_plan"],
        "properties": {"release_plan": {"type": "object"}},
    },
    allowed_capabilities=frozenset({"release_analysis", "operations_analysis", "composition"}),
    default_planner_mode="fixed",
    validation_policy={
        "baseline_validators": ["release_plan_contract", "secret_scan"],
        "write_grants": "none",
        "outcomes": ["ready", "blocked", "needs_decision"],
    },
    skill_policy={
        "grant_enforcement": "fail_closed",
        "allow": ["release.readiness-review"],
    },
    routing_defaults={
        "release_analysis_profile": "fast_worker",
        "operations_analysis_profile": "fast_worker",
    },
    execution_policy=execution_policy(
        capabilities=frozenset({"release_analysis", "operations_analysis", "composition"}),
        validators=["release_plan_contract", "secret_scan"],
        output_roles=(ROLE_RELEASE_PLAN,),
        denied_tool_names=frozenset(
            {
                "create_file",
                "apply_patch",
                "resolve_deployment_target",
                "start_deployment",
                "get_rollout_status",
                "verify_health",
                "rollback_deployment",
            }
        ),
        fallback_composition_roles=frozenset({ROLE_RELEASE_PLAN}),
        required_output_roles=frozenset({ROLE_RELEASE_PLAN}),
        evaluation_fixture_id="release_readiness.v1",
    ),
    artifacts=(RELEASE_PLAN_LAND_SPEC,),
    description=(
        "Read-only release evidence review over immutable Git/CI and bounded "
        "operational signals; emits ready, blocked, or needs_decision."
    ),
)

__all__ = ["RELEASE_READINESS_PACK"]
