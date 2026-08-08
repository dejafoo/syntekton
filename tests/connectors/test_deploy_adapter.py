from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.connectors.deploy import (
    DeploymentTarget,
    DeploymentTargetRegistry,
    SimulatedStagingAdapter,
)
from product_factory.connectors.errors import ConnectorPolicyDenied


def _adapter(path: Path) -> SimulatedStagingAdapter:
    registry = DeploymentTargetRegistry(
        [DeploymentTarget(target_id="staging-a", environment="staging")]
    )
    return SimulatedStagingAdapter(registry, state_path=path)


def _start(adapter: SimulatedStagingAdapter, *, key: str = "release-1"):
    return adapter.start(
        target_id="staging-a",
        release_plan_digest="a" * 64,
        artifact_digest="b" * 64,
        idempotency_key=key,
        change_window={"start": "2026-01-01T00:00:00Z"},
        approved=True,
    )


def test_no_effect_without_approval(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path / "deployments.json")
    with pytest.raises(ConnectorPolicyDenied, match="approval"):
        adapter.start(
            target_id="staging-a",
            release_plan_digest="a" * 64,
            artifact_digest="b" * 64,
            idempotency_key="release-1",
            change_window={},
            approved=False,
        )
    assert not (tmp_path / "deployments.json").exists()


def test_idempotent_start_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "deployments.json"
    first = _start(_adapter(path))
    second = _start(_adapter(path))
    assert second.deployment_id == first.deployment_id
    assert second.details["replayed"] is True


def test_target_concurrency_lock_and_failed_health_rollback(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path / "deployments.json")
    deployment = _start(adapter)
    with pytest.raises(ConnectorPolicyDenied, match="active rollout"):
        _start(adapter, key="release-2")
    halted = adapter.verify_health(deployment.deployment_id or "", healthy=False)
    assert halted.status == "halted"
    rolled_back = adapter.rollback(deployment.deployment_id or "", approved=True)
    assert rolled_back.status == "rolled_back"
    assert rolled_back.details["rollback_result"]["status"] == "rolled_back"


def test_production_target_is_rejected_at_registry_boundary() -> None:
    with pytest.raises(ValueError, match="production"):
        DeploymentTarget(target_id="prod", environment="production")
