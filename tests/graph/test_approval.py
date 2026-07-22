"""Approval and apply gate tests."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import ApprovalBlockedError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from tests.conftest import clone_fixture


def test_cannot_apply_without_approval(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    fixture = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    manifest = coord.run(
        RunRequest(
            request_id="req-1",
            workflow_type="code_change",
            request_text="Add health endpoint",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        )
    )
    with pytest.raises(ApprovalBlockedError):
        coord.apply_patch(manifest.run_id)
    coord.approve(manifest.run_id, apply=False)
    # Applying to fixture would dirty it; just ensure approve succeeds.
    assert (
        tmp_path / ".product-factory" / "runs" / manifest.run_id / "output" / "approval.json"
    ).exists()
