from __future__ import annotations

import json

from product_factory.domain.runs import RunRequest
from product_factory.validation.pipeline import validate_deployment_record
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.handlers.base import ComposeContext


def _input() -> dict[str, object]:
    return {
        "release_plan": {"outcome": "ready"},
        "release_plan_digest": "a" * 64,
        "artifact_digest": "b" * 64,
        "target_id": "simulated-local",
        "environment": "staging",
        "change_window": {"start": "2026-01-01T00:00:00Z"},
        "idempotency_key": "release-1",
        "approval_binding": {
            "approval_id": "approval-1",
            "release_plan_digest": "a" * 64,
            "artifact_digest": "b" * 64,
            "target_id": "simulated-local",
            "change_window": {"start": "2026-01-01T00:00:00Z"},
        },
        "deployment_outcome": "succeeded",
        "action_log": [{"action": "start_deployment", "status": "started"}],
        "health_checks": [{"name": "rollout", "healthy": True}],
    }


def _document(data: dict[str, object]) -> str:
    request = RunRequest(
        request_id="req-deploy-receipt",
        workflow_type="deployment_execution",
        request_text="deploy",
        pack_input=data,
    )
    return handler_for("deployment_execution").compose(
        "deployment_record",
        ComposeContext(
            request=request,
            role="deployment_record",
            document_name="DEPLOYMENT_RECORD.json",
        ),
    )


def test_deployment_receipt_binds_approval_and_immutable_artifact() -> None:
    document = _document(_input())
    assert validate_deployment_record(document).status == "pass"
    payload = json.loads(document)
    assert payload["approval_binding"]["artifact_digest"] == payload["artifact_digest"]


def test_halted_receipt_requires_rollback_result() -> None:
    data = _input()
    data["deployment_outcome"] = "halted"
    data["health_checks"] = [{"name": "rollout", "healthy": False}]
    result = validate_deployment_record(_document(data))
    assert result.status == "fail"
    assert "rollback result" in str(result.details)
