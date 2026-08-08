from __future__ import annotations

import json

from product_factory.domain.runs import RunRequest
from product_factory.validation.pipeline import validate_release_plan
from product_factory.workflows.artifacts import ROLE_RELEASE_PLAN
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.handlers.release_readiness import ReleaseReadinessHandler

_ANALYSIS_DEPS = [
    {
        "task_id": "T-001",
        "artifact_refs": [{"logical_name": "release-analysis-T-001.json"}],
    },
    {
        "task_id": "T-002",
        "artifact_refs": [{"logical_name": "operations-analysis-T-002.json"}],
    },
]


def _compose(**pack_input: object) -> str:
    request = RunRequest(
        request_id="req-release",
        workflow_type="release_readiness",
        request_text="Assess v1",
        pack_input=pack_input,
        approval_policy="none",
    )
    return ReleaseReadinessHandler().compose(
        ROLE_RELEASE_PLAN,
        ComposeContext(
            request=request,
            role=ROLE_RELEASE_PLAN,
            document_name="RELEASE_PLAN.json",
            dependency_outputs=_ANALYSIS_DEPS,
        ),
    )


def test_ready_requires_verification_migration_rollback_and_digest_pins() -> None:
    document = _compose(
        input_digests={"change_set": "a" * 64},
        version="1.0.0",
        verification_evidence=["validation:abc"],
        migration_preconditions=["not required: schema unchanged"],
        rollback_criteria=[{"criterion": "error rate > 1%", "evidence_ref": "monitor:abc"}],
    )
    assert json.loads(document)["outcome"] == "ready"
    assert validate_release_plan(document).status == "pass"


def test_ready_blocked_without_analysis_receipts() -> None:
    request = RunRequest(
        request_id="req-release-no-analysis",
        workflow_type="release_readiness",
        request_text="Assess v1",
        pack_input={
            "input_digests": {"change_set": "a" * 64},
            "verification_evidence": ["validation:abc"],
            "migration_preconditions": ["not required"],
            "rollback_criteria": [{"criterion": "error rate > 1%", "evidence_ref": "monitor:abc"}],
        },
        approval_policy="none",
    )
    document = ReleaseReadinessHandler().compose(
        ROLE_RELEASE_PLAN,
        ComposeContext(
            request=request,
            role=ROLE_RELEASE_PLAN,
            document_name="RELEASE_PLAN.json",
            dependency_outputs=[],
        ),
    )
    assert json.loads(document)["outcome"] == "blocked"


def test_missing_rollback_blocks_and_unresolved_choice_needs_decision() -> None:
    blocked = _compose(
        input_digests={"change_set": "a" * 64},
        verification_evidence=["validation:abc"],
        migration_preconditions=["not required"],
    )
    assert json.loads(blocked)["outcome"] == "blocked"
    decision = _compose(
        input_digests={"change_set": "a" * 64},
        verification_evidence=["validation:abc"],
        migration_preconditions=["not required"],
        rollback_criteria=[{"criterion": "health regression"}],
        unresolved_decisions=["approve compatibility exception"],
    )
    assert json.loads(decision)["outcome"] == "needs_decision"
    assert validate_release_plan(decision).status == "pass"


def test_validator_rejects_ready_without_required_evidence() -> None:
    payload = json.loads(_compose(input_digests={"change_set": "a" * 64}))
    payload["outcome"] = "ready"
    result = validate_release_plan(json.dumps(payload))
    assert result.status == "fail"
