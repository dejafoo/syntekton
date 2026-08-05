from __future__ import annotations

import json
from pathlib import Path

from product_factory.domain.runs import RunRequest
from product_factory.planning.compiler import compile_plan
from product_factory.validation.pipeline import validate_operational_record
from product_factory.workflows.artifacts import ROLE_OPERATIONAL_RECORD
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.registry import resolve_workflow_pack


def test_service_health_review_is_registered_and_compiles_read_only() -> None:
    pack = resolve_workflow_pack("service_health_review")
    handler = handler_for(pack.id)
    assert handler.authority_class() == "external_read"
    proposal = handler.plan_template("Review checkout health")
    result = compile_plan(proposal, workflow_pack=pack)
    assert result.ok, result.errors
    assert all(task.capability != "deployment_execution" for task in proposal.tasks)
    assert "restart_service" in pack.execution_policy.denied_tool_names


def test_slo_breach_composes_change_intake_follow_up() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "tests/fixtures/ops/health_slo_breach.json").read_text(encoding="utf-8")
    )
    request = RunRequest(
        request_id="req-health",
        workflow_type="service_health_review",
        request_text="Review checkout health",
        pack_input=fixture,
        approval_policy="none",
    )
    document = handler_for("service_health_review").compose(
        ROLE_OPERATIONAL_RECORD,
        ComposeContext(
            request=request,
            role=ROLE_OPERATIONAL_RECORD,
            document_name="OPERATIONAL_RECORD.json",
        ),
    )
    payload = json.loads(document)
    assert payload["record_type"] == "service_health_review"
    assert payload["follow_up"] == "change_intake"
    assert payload["authority"] == {
        "class": "external_read",
        "deploy": False,
        "restart": False,
        "traffic_mutation": False,
    }
    assert validate_operational_record(document).status == "pass"


def test_validator_rejects_inference_mislabeled_as_observation() -> None:
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "tests/fixtures/ops/health_slo_breach.json").read_text(encoding="utf-8")
    )
    request = RunRequest(
        request_id="req-health-invalid",
        workflow_type="service_health_review",
        request_text="Review checkout health",
        pack_input=payload,
        approval_policy="none",
    )
    document = handler_for("service_health_review").compose(
        ROLE_OPERATIONAL_RECORD,
        ComposeContext(
            request=request,
            role=ROLE_OPERATIONAL_RECORD,
            document_name="OPERATIONAL_RECORD.json",
        ),
    )
    record = json.loads(document)
    record["hypotheses"][0]["label"] = "observation"
    assert validate_operational_record(json.dumps(record)).status == "fail"
