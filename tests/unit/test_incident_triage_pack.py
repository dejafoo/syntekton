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


def test_incident_triage_is_registered_external_read_only() -> None:
    pack = resolve_workflow_pack("incident_triage")
    handler = handler_for(pack.id)
    assert handler.authority_class() == "external_read"
    assert pack.allowed_capabilities == {"operations_analysis", "composition"}
    assert "operational_record_contract" in pack.execution_policy.validators
    assert {
        "start_deployment",
        "rollback_deployment",
        "restart_service",
        "shift_traffic",
    } <= pack.execution_policy.denied_tool_names


def test_incident_triage_plan_compiles_without_mutation_tools() -> None:
    pack = resolve_workflow_pack("incident_triage")
    proposal = handler_for(pack.id).plan_template("Triage INC-42")
    result = compile_plan(proposal, workflow_pack=pack)
    assert result.ok, result.errors
    for task in proposal.tasks:
        assert task.capability != "deployment_execution"
        assert not (
            task.required_tool_classes
            & {"repository_write", "git_write", "deployment_read", "deployment_write"}
        )


def test_known_incident_composes_labeled_typed_record() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "tests/fixtures/ops/incident_known.json").read_text(encoding="utf-8")
    )
    request = RunRequest(
        request_id="req-incident",
        workflow_type="incident_triage",
        request_text="Triage INC-42",
        pack_input=fixture,
        approval_policy="none",
    )
    document = handler_for("incident_triage").compose(
        ROLE_OPERATIONAL_RECORD,
        ComposeContext(
            request=request,
            role=ROLE_OPERATIONAL_RECORD,
            document_name="OPERATIONAL_RECORD.json",
        ),
    )
    payload = json.loads(document)
    assert payload["record_type"] == "incident_triage"
    assert {item["label"] for item in payload["evidence"]} == {"observation"}
    assert {item["label"] for item in payload["hypotheses"]} == {"inference"}
    assert payload["follow_up_action"]["type"] == "rollback_decision"
    assert payload["follow_up_action"]["requires_human"] is True
    assert validate_operational_record(document).status == "pass"
