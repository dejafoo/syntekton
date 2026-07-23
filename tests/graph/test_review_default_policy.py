"""Review default policy: optional except high-risk fixed plans."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_low_risk_default_plan_omits_review() -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=root / ".product-factory-test-unused",
        use_deterministic_planner=True,
    )
    proposal = coordinator._plan(
        "run",
        RunRequest(
            request_id="low-risk",
            workflow_type="code_change",
            request_text="Add a cache helper",
            approval_policy="none",
            metadata={"planner_mode": "fixed"},
        ),
        None,
    )
    caps = {task.capability for task in proposal.tasks}
    assert "independent_review" not in caps
    assert "implementation" in caps


def test_high_risk_fixed_plan_keeps_review() -> None:
    root = Path(__file__).resolve().parents[2]
    coordinator = RunCoordinator(
        config=load_config(root),
        gateway=MockGateway(),
        data_dir=root / ".product-factory-test-unused",
        use_deterministic_planner=True,
    )
    proposal = coordinator._plan(
        "run",
        RunRequest(
            request_id="high-risk",
            workflow_type="code_change",
            request_text="Change authentication and database permissions",
            approval_policy="none",
            metadata={"planner_mode": "fixed"},
        ),
        None,
    )
    caps = {task.capability for task in proposal.tasks}
    assert "independent_review" in caps
