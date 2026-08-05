"""Runtime behavior for the controlled deployment-execution pack."""

from __future__ import annotations

import json
from typing import Any

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_DEPLOYMENT_RECORD
from product_factory.workflows.default_plans import default_deployment_execution_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


def _objects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


class DeploymentExecutionHandler:
    pack_id = "deployment_execution"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_deployment_execution_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_DEPLOYMENT_RECORD:
            raise RuntimeError(f"deployment_execution does not compose role {role!r}")
        data = ctx.pack_input
        action_log = _objects(data.get("action_log"))
        health_checks = _objects(data.get("health_checks"))
        rollback = data.get("rollback_result")
        outcome = str(data.get("deployment_outcome") or "unknown")
        if rollback and isinstance(rollback, dict):
            outcome = "rolled_back" if rollback.get("status") == "rolled_back" else outcome
        elif any(
            item.get("healthy") is False or item.get("passed") is False for item in health_checks
        ):
            outcome = "halted"
        elif health_checks and all(
            item.get("healthy", item.get("passed")) is True for item in health_checks
        ):
            outcome = "succeeded"
        payload = {
            "schema_id": "deployment_record.v1",
            "release_plan_digest": str(data.get("release_plan_digest") or "").lower(),
            "artifact_digest": str(data.get("artifact_digest") or "").lower(),
            "target_id": str(data.get("target_id") or ""),
            "environment": str(data.get("environment") or "staging"),
            "outcome": outcome,
            "action_log": action_log,
            "health_checks": health_checks,
            "observed_metrics": _objects(data.get("observed_metrics")),
            "policy_decisions": _objects(data.get("policy_decisions"))
            or [
                {
                    "decision": "non_production_target_only",
                    "result": "allow",
                    "target_id": data.get("target_id"),
                },
                {
                    "decision": "immutable_approval_binding",
                    "result": "allow" if data.get("approval_binding") else "deny",
                    "approval_binding": data.get("approval_binding"),
                },
            ],
            "rollback_result": rollback if isinstance(rollback, dict) else None,
            "idempotency_key": str(data.get("idempotency_key") or ""),
            "change_window": dict(data.get("change_window") or {}),
            "approval_binding": dict(data.get("approval_binding") or {}),
            "reconciliation": dict(data.get("reconciliation") or {}),
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ()

    def validator_id(self, role: str) -> str:
        return "deployment_record_contract"

    def authority_class(self) -> AuthorityClass:
        return "external_write"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="service_health_review",
                reason="Review post-deployment health using the read-only operations plane",
            )
        ]

    def findings_are_deliverable(self) -> bool:
        return True


__all__ = ["DeploymentExecutionHandler"]
