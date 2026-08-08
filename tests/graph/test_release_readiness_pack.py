from __future__ import annotations

import json
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def _coordinator(tmp_path: Path, *, enable: tuple[str, ...] = ()) -> RunCoordinator:
    project_root = Path(__file__).resolve().parents[2]
    config = load_config(project_root)
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


def test_mock_release_readiness_emits_typed_plan_without_write_or_deploy_tools(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / ".product-factory"
    coordinator = _coordinator(tmp_path, enable=("git_ci_read", "ops_read"))
    manifest = coordinator.run(
        RunRequest(
            request_id="req-release-ready",
            workflow_type="release_readiness",
            request_text="Assess immutable candidate 1.2.3 for release.",
            pack_input={
                "repository": "acme/service",
                "commit_sha": "a" * 40,
                "version": "1.2.3",
                "service_id": "checkout",
                "environment": "staging",
                "time_window": {
                    "start": "2026-08-01T10:00:00Z",
                    "end": "2026-08-01T10:15:00Z",
                },
                "input_digests": {"change_set": "b" * 64},
                "verification_evidence": ["validation-evidence:" + "c" * 64],
                "migration_preconditions": ["not required: no schema changes"],
                "rollback_criteria": [
                    {"criterion": "error rate exceeds 1%", "evidence_ref": "monitor:error-rate"}
                ],
            },
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    payload = json.loads(
        (data_dir / "runs" / manifest.run_id / "output" / "RELEASE_PLAN.json").read_text()
    )
    assert payload["outcome"] == "ready"
    calls = coordinator.db.list_tool_calls(manifest.run_id)
    tool_names = {row["tool_name"] for row in calls}
    assert not {
        "create_file",
        "apply_patch",
        "start_deployment",
        "rollback_deployment",
    } & tool_names
    # Fake-live: analysis must exercise CI/ops connectors, not succeed as a stub.
    assert "get_commit_checks" in tool_names
    assert "query_service_signals" in tool_names


def test_release_readiness_blocks_without_ci_connector(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path)
    try:
        manifest = coordinator.run(
            RunRequest(
                request_id="req-release-blocked",
                workflow_type="release_readiness",
                request_text="Assess candidate without CI.",
                pack_input={
                    "repository": "acme/service",
                    "commit_sha": "a" * 40,
                    "version": "1.2.3",
                    "input_digests": {"change_set": "b" * 64},
                    "verification_evidence": ["validation-evidence:" + "c" * 64],
                    "migration_preconditions": ["not required"],
                    "rollback_criteria": [{"criterion": "error rate", "evidence_ref": "monitor:x"}],
                },
                approval_policy="none",
                metadata={"disable_review": "true", "planner_mode": "fixed"},
            )
        )
    except Exception as exc:  # RuntimeFailureError from blocked analysis
        assert "release_analysis blocked" in str(exc) or "Dependency failed" in str(exc)
        return
    assert manifest.final_status in {"failed", "blocked"}
