"""SD1.E fake-live coverage for canonical packs — assert executor receipts."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.executors.registry import default_executor_registry
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.registry.capability_descriptors import CAPABILITY_DESCRIPTORS
from product_factory.workflows.base import EXECUTOR_MODES
from product_factory.workflows.registry import list_workflow_packs
from tests.conftest import clone_fixture


def _coord(tmp_path: Path, *, enable: tuple[str, ...] = ()) -> RunCoordinator:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    if enable:
        settings = dict(config.connectors.connectors)
        for connector_id in enable:
            settings[connector_id] = ConnectorSettings(enabled=True)
        config = config.model_copy(
            update={"connectors": config.connectors.model_copy(update={"connectors": settings})}
        )
    return RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_registry_covers_every_descriptor_mode_and_adapter() -> None:
    registry = default_executor_registry()
    for mode in EXECUTOR_MODES:
        registry.require(mode)
    for descriptor in CAPABILITY_DESCRIPTORS.values():
        executor = registry.require(descriptor.executor_mode)
        assert descriptor.executor_adapter_id in executor.adapter_ids


@pytest.mark.parametrize("pack", list_workflow_packs(), ids=lambda p: p.id)
def test_every_canonical_pack_has_executor_modes_for_capabilities(pack) -> None:
    for capability in pack.allowed_capabilities:
        mode = pack.execution_policy.executor_mode_for(capability)
        assert mode == CAPABILITY_DESCRIPTORS[capability].executor_mode


def test_fake_live_release_readiness_records_ci_and_ops_activity(tmp_path: Path) -> None:
    coord = _coord(tmp_path, enable=("git_ci_read", "ops_read"))
    manifest = coord.run(
        RunRequest(
            request_id="req-sd1-release",
            workflow_type="release_readiness",
            request_text="Assess release candidate.",
            pack_input={
                "repository": "acme/service",
                "commit_sha": "a" * 40,
                "version": "1.0.0",
                "service_id": "checkout",
                "environment": "staging",
                "time_window": {
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T10:15:00Z",
                },
                "input_digests": {"change_set": "b" * 64},
                "verification_evidence": ["validation-evidence:" + "c" * 64],
                "migration_preconditions": ["none"],
                "rollback_criteria": [{"criterion": "errors", "evidence_ref": "monitor:x"}],
            },
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed"
    tasks = coord.db.list_tasks(manifest.run_id)
    analysis = [
        json.loads(row["result_json"])
        for row in tasks
        if row["capability"] in {"release_analysis", "operations_analysis"}
    ]
    assert analysis
    for result in analysis:
        assert result.get("execution_mode") in {"live", "deterministic_mock"}
        assert result.get("executor_adapter_id")
        assert result.get("activity_receipt")
    tools = {row["tool_name"] for row in coord.db.list_tool_calls(manifest.run_id)}
    assert "get_commit_checks" in tools
    assert "query_service_signals" in tools


def test_fake_live_quality_gate_marks_mock_execution(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    repo = clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = _coord(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-sd1-qg",
            workflow_type="quality_gate",
            request_text="Assess quality.",
            repository_path=repo,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
        )
    )
    assert manifest.final_status == "completed"
    for row in coord.db.list_tasks(manifest.run_id):
        if not row.get("result_json"):
            continue
        result = json.loads(row["result_json"])
        if row["capability"] in {
            "test_design",
            "security_review",
            "test_execution",
            "independent_review",
            "composition",
        }:
            assert result.get("executor_mode")
            assert result.get("execution_mode") == "deterministic_mock"
            assert result.get("executor_adapter_id")
