from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from product_factory.domain.artifacts import HandoffRef, HandoffState
from product_factory.domain.errors import SchemaValidationError
from product_factory.domain.runs import RunRequest
from product_factory.schemas.validate import validate_write_payload
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.handlers.repository_change import RepositoryChangeHandler
from product_factory.workflows.handoffs import validate_pack_handoffs
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK

FIXTURE = Path(__file__).parents[1] / "fixtures" / "change" / "proposed.patch"


def _request(
    *, role: str = "architecture_document", state: HandoffState = "approved"
) -> RunRequest:
    return RunRequest(
        request_id="pm4a-change",
        workflow_type="repository_change",
        request_text="Implement the pinned plan.",
        handoff_refs=[
            HandoffRef(
                schema_id="technical_plan.document.v2",
                digest="a" * 64,
                producer_run_id="run-plan",
                producer_task_id="T-003",
                role=role,
                state=state,
            )
        ],
    )


def test_repository_change_v2_emits_content_addressed_change_set() -> None:
    patch = FIXTURE.read_text(encoding="utf-8")
    request = _request()
    document = RepositoryChangeHandler().compose(
        "change_set",
        ComposeContext(
            request=request,
            role="change_set",
            document_name="change-set.json",
            dependency_outputs=[
                {
                    "artifact_excerpts": [
                        {"logical_name": "proposed.patch", "content": patch}
                    ]
                }
            ],
            run_id="run-change",
            base_revision="deadbeef",
            validation_evidence_refs=["e" * 64],
        ),
    )
    payload = json.loads(document)

    validate_write_payload("change_set.v1", payload)
    assert payload["base_revision"] == "deadbeef"
    assert payload["patch_sha256"] == hashlib.sha256(patch.encode()).hexdigest()
    assert payload["artifact_hashes"]["proposed.patch"] == payload["patch_sha256"]
    assert payload["changed_paths"] == ["src/example.py"]
    assert payload["acceptance_refs"] == [f"technical_plan.document.v2:{'a' * 64}"]
    assert payload["validation_evidence_refs"] == ["e" * 64]
    assert payload["producer_run_id"] == "run-change"


@pytest.mark.parametrize(
    ("role", "state"),
    [("change_brief", "approved"), ("architecture_document", "draft")],
)
def test_repository_change_fails_closed_on_bad_plan_pin(
    role: str, state: HandoffState
) -> None:
    with pytest.raises(SchemaValidationError):
        validate_pack_handoffs(_request(role=role, state=state), REPOSITORY_CHANGE_PACK)
