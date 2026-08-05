from __future__ import annotations

import os
from pathlib import Path

import pytest

from product_factory.connectors.deploy import (
    DeploymentTarget,
    DeploymentTargetRegistry,
    StagingDeployAdapter,
)

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("DEPLOY_INTEGRATION") != "1",
    reason="set DEPLOY_INTEGRATION=1 for the opt-in staging adapter smoke",
)
def test_staging_restart_reconciles_without_duplicate_effect(tmp_path: Path) -> None:
    state = tmp_path / "staging-state.json"
    registry = DeploymentTargetRegistry(
        [DeploymentTarget(target_id="staging-live", environment="staging")]
    )
    first = StagingDeployAdapter(registry, state_path=state).start(
        target_id="staging-live",
        release_plan_digest="a" * 64,
        artifact_digest="b" * 64,
        idempotency_key="live-smoke-1",
        change_window={"start": "now"},
        approved=True,
    )
    reconciled = StagingDeployAdapter(registry, state_path=state).start(
        target_id="staging-live",
        release_plan_digest="a" * 64,
        artifact_digest="b" * 64,
        idempotency_key="live-smoke-1",
        change_window={"start": "now"},
        approved=True,
    )
    assert reconciled.deployment_id == first.deployment_id
    assert reconciled.details["replayed"] is True
