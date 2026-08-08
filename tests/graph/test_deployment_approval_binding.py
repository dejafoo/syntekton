from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.errors import ApprovalBlockedError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def _input(*, approved: bool) -> dict[str, object]:
    data: dict[str, object] = {
        "release_plan": {"outcome": "ready"},
        "release_plan_digest": "a" * 64,
        "artifact_digest": "b" * 64,
        "target_id": "staging-local",
        "change_window": {"start": "2026-01-01T00:00:00Z"},
        "idempotency_key": "graph-release-1",
    }
    if approved:
        data["approval_binding"] = {
            "approval_id": "approval-graph-1",
            "release_plan_digest": "a" * 64,
            "artifact_digest": "b" * 64,
            "target_id": "staging-local",
            "change_window": {"start": "2026-01-01T00:00:00Z"},
        }
    return data


def _coordinator(tmp_path: Path) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    settings = dict(config.connectors.connectors)
    settings["staging_deploy"] = ConnectorSettings(enabled=True)
    config = config.model_copy(
        update={
            "connectors": config.connectors.model_copy(
                update={"allow_write_connectors": True, "connectors": settings}
            )
        }
    )
    return RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_approval_block_occurs_before_any_connector_call(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    with pytest.raises(ApprovalBlockedError):
        coordinator.run(
            RunRequest(
                request_id="req-deploy-unapproved",
                workflow_type="deployment_execution",
                request_text="Deploy candidate to staging",
                pack_input=_input(approved=False),
                metadata={"planner_mode": "fixed"},
            )
        )
    run = coordinator.db.list_runs(limit=1)[0]
    assert run["status"] == "awaiting_approval"
    assert coordinator.db.list_tool_calls(run["run_id"]) == []


def test_forged_pack_input_binding_cannot_authorize_deployment(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    with pytest.raises(ApprovalBlockedError):
        coordinator.run(
            RunRequest(
                request_id="req-deploy-forged-binding",
                workflow_type="deployment_execution",
                request_text="Deploy candidate to staging",
                pack_input=_input(approved=True),
                metadata={"planner_mode": "fixed"},
            )
        )
    run = coordinator.db.list_runs(limit=1)[0]
    assert run["status"] == "awaiting_approval"
    assert coordinator.db.list_tool_calls(run["run_id"]) == []


def test_durable_action_approval_authorizes_before_connector(tmp_path: Path) -> None:
    from product_factory.trust.approvals import ApprovalService, deployment_action_fingerprint

    coordinator = _coordinator(tmp_path)
    data = _input(approved=False)
    fingerprint = deployment_action_fingerprint(
        release_handoff_id="handoff-release",
        release_handoff_digest="d" * 64,
        release_plan_digest=str(data["release_plan_digest"]),
        artifact_digest=str(data["artifact_digest"]),
        target_id=str(data["target_id"]),
        change_window=data["change_window"],
        idempotency_key=str(data["idempotency_key"]),
    )
    # Subject run must exist for FK; use a placeholder then the consumer run will differ.
    coordinator.db.upsert_run(
        run_id="subject-release",
        workflow_type="release_readiness",
        status="completed",
        request={},
    )
    approval = ApprovalService(coordinator.db).create_pending(
        action_type="staging_deploy",
        subject_run_id="subject-release",
        action_fingerprint=fingerprint,
        actor="operator",
        payload={"target_id": data["target_id"]},
    )
    ApprovalService(coordinator.db).decide(approval.approval_id, "approved", "operator")
    data["approval_binding"] = {
        "approval_id": approval.approval_id,
        "release_handoff_id": "handoff-release",
        "release_handoff_digest": "d" * 64,
        "release_plan_digest": data["release_plan_digest"],
        "artifact_digest": data["artifact_digest"],
        "target_id": data["target_id"],
        "change_window": data["change_window"],
    }
    data["release_handoff_id"] = "handoff-release"
    data["release_handoff_digest"] = "d" * 64
    manifest = coordinator.run(
        RunRequest(
            request_id="req-deploy-durable",
            workflow_type="deployment_execution",
            request_text="Deploy candidate to staging",
            pack_input=data,
            metadata={"planner_mode": "fixed"},
        )
    )
    assert manifest.final_status in {"completed", "awaiting_approval", "failed", "blocked"}
    # Authority consumed: connector path was allowed past the approval gate.
    consumed = ApprovalService(coordinator.db).get(approval.approval_id)
    assert consumed is not None
    assert consumed.status == "consumed"
