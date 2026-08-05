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
