"""Handoff validation boundary tests (PM0.A)."""

from __future__ import annotations

import pytest

from product_factory.domain.errors import SchemaValidationError
from product_factory.domain.runs import RunRequest
from product_factory.workflows.handoffs import validate_request_handoffs


def test_valid_handoff_accepted() -> None:
    req = RunRequest(
        request_id="r1",
        workflow_type="repository_change",
        request_text="implement",
        handoff_refs=[
            {
                "schema_id": "technical_plan.document.v1",
                "digest": "a" * 64,
                "producer_run_id": "run-1",
                "producer_task_id": "T-003",
                "role": "architecture_document",
                "state": "approved",
            }
        ],
    )
    refs = validate_request_handoffs(req)
    assert len(refs) == 1
    assert refs[0].schema_id == "technical_plan.document.v1"


def test_malformed_handoff_fails() -> None:
    with pytest.raises(Exception):
        RunRequest(
            request_id="r1",
            workflow_type="repository_change",
            request_text="implement",
            handoff_refs=[{"schema_id": "technical_plan.document.v1"}],
        )
    with pytest.raises(SchemaValidationError):
        validate_request_handoffs(
            {
                "handoff_refs": [{"schema_id": "technical_plan.document.v1"}],
            }
        )
