from __future__ import annotations

import json
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.trust.approvals import ApprovalService, deployment_action_fingerprint


def test_mock_deployment_executes_staging_and_emits_record(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    settings = dict(config.connectors.connectors)
    settings["simulated_staging"] = ConnectorSettings(enabled=True)
    config = config.model_copy(
        update={
            "connectors": config.connectors.model_copy(
                update={"allow_write_connectors": True, "connectors": settings}
            )
        }
    )
    coordinator = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    pack_input = {
        "release_plan": {"outcome": "ready"},
        "release_plan_digest": "a" * 64,
        "artifact_digest": "b" * 64,
        "target_id": "simulated-local",
        "change_window": {"start": "2026-01-01T00:00:00Z"},
        "idempotency_key": "graph-release-happy",
        "release_handoff_id": "handoff-release-happy",
        "release_handoff_digest": "c" * 64,
    }
    fingerprint = deployment_action_fingerprint(
        release_handoff_id=str(pack_input["release_handoff_id"]),
        release_handoff_digest=str(pack_input["release_handoff_digest"]),
        release_plan_digest=str(pack_input["release_plan_digest"]),
        artifact_digest=str(pack_input["artifact_digest"]),
        target_id=str(pack_input["target_id"]),
        change_window=pack_input["change_window"],
        idempotency_key=str(pack_input["idempotency_key"]),
    )
    coordinator.db.upsert_run(
        run_id="subject-release-happy",
        workflow_type="release_readiness",
        status="completed",
        request={},
    )
    service = ApprovalService(coordinator.db)
    approval = service.create_pending(
        action_type="simulated_staging",
        subject_run_id="subject-release-happy",
        action_fingerprint=fingerprint,
        actor="operator",
        payload={"target_id": pack_input["target_id"]},
    )
    service.decide(approval.approval_id, "approved", "operator")
    pack_input["approval_binding"] = {
        "approval_id": approval.approval_id,
        "release_handoff_id": pack_input["release_handoff_id"],
        "release_handoff_digest": pack_input["release_handoff_digest"],
        # Projection fields for the deployment record only — authority is the
        # durable ActionApproval fingerprint, not these mirrored values.
        "release_plan_digest": pack_input["release_plan_digest"],
        "artifact_digest": pack_input["artifact_digest"],
        "target_id": pack_input["target_id"],
        "change_window": pack_input["change_window"],
    }
    manifest = coordinator.run(
        RunRequest(
            request_id="req-deploy-happy",
            workflow_type="deployment_execution",
            request_text="Deploy approved candidate to staging",
            pack_input=pack_input,
            approval_policy="none",
            metadata={"planner_mode": "fixed", "disable_review": "true"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    record = json.loads(
        (
            tmp_path
            / ".product-factory"
            / "runs"
            / manifest.run_id
            / "output"
            / "DEPLOYMENT_RECORD.json"
        ).read_text()
    )
    assert record["outcome"] == "succeeded"
    assert record["artifact_digest"] == "b" * 64
    called = {row["tool_name"] for row in coordinator.db.list_tool_calls(manifest.run_id)}
    assert {"resolve_deployment_target", "start_deployment", "verify_health"} <= called
    consumed = service.get(approval.approval_id)
    assert consumed is not None
    assert consumed.status == "consumed"
