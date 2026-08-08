"""SD0.C durable external-action approval tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from product_factory.persistence.database import Database
from product_factory.trust.approvals import (
    ApprovalError,
    ApprovalService,
    deployment_action_fingerprint,
)


def _fingerprint() -> str:
    return deployment_action_fingerprint(
        release_handoff_id="handoff-release",
        release_handoff_digest="a" * 64,
        release_plan_digest="b" * 64,
        artifact_digest="c" * 64,
        target_id="staging",
        change_window={"start": "2026-08-08T00:00:00Z"},
        idempotency_key="deploy-1",
    )


def _service(tmp_path: Path) -> ApprovalService:
    db = Database(tmp_path / "db.sqlite")
    db.upsert_run(run_id="subject", workflow_type="test", status="completed", request={})
    db.upsert_run(run_id="consumer", workflow_type="test", status="queued", request={})
    return ApprovalService(db)


def test_pack_input_boolean_cannot_consume_approval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approval = service.create_pending(
        action_type="deploy",
        subject_run_id="subject",
        action_fingerprint=_fingerprint(),
        actor="operator",
    )

    with pytest.raises(ApprovalError):
        service.consume_for_execution(
            approval.approval_id, expected_fingerprint=_fingerprint(), consumer_run_id="consumer"
        )


def test_changed_or_expired_action_is_refused(tmp_path: Path) -> None:
    service = _service(tmp_path)
    approval = service.create_pending(
        action_type="deploy",
        subject_run_id="subject",
        action_fingerprint=_fingerprint(),
        actor="operator",
        expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    )
    with pytest.raises(ApprovalError):
        service.decide(approval.approval_id, "approved", "operator")

    fresh = service.create_pending(
        action_type="deploy",
        subject_run_id="subject",
        action_fingerprint=_fingerprint(),
        actor="operator",
    )
    service.decide(fresh.approval_id, "approved", "operator")
    with pytest.raises(ApprovalError):
        service.consume_for_execution(
            fresh.approval_id, expected_fingerprint="0" * 64, consumer_run_id="consumer"
        )
