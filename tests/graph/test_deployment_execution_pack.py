from __future__ import annotations

import json
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_mock_deployment_executes_staging_and_emits_record(tmp_path: Path) -> None:
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
    coordinator = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="req-deploy-happy",
            workflow_type="deployment_execution",
            request_text="Deploy approved candidate to staging",
            pack_input={
                "release_plan": {"outcome": "ready"},
                "release_plan_digest": "a" * 64,
                "artifact_digest": "b" * 64,
                "target_id": "staging-local",
                "change_window": {"start": "2026-01-01T00:00:00Z"},
                "idempotency_key": "graph-release-happy",
                "approval_binding": {
                    "approval_id": "approval-happy",
                    "release_plan_digest": "a" * 64,
                    "artifact_digest": "b" * 64,
                    "target_id": "staging-local",
                    "change_window": {"start": "2026-01-01T00:00:00Z"},
                },
            },
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
