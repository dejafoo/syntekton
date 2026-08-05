from __future__ import annotations

import json
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_mock_release_readiness_emits_typed_plan_without_write_or_deploy_tools(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    data_dir = tmp_path / ".product-factory"
    coordinator = RunCoordinator(
        config=load_config(project_root),
        gateway=MockGateway(),
        data_dir=data_dir,
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="req-release-ready",
            workflow_type="release_readiness",
            request_text="Assess immutable candidate 1.2.3 for release.",
            pack_input={
                "repository": "acme/service",
                "commit_sha": "a" * 40,
                "version": "1.2.3",
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
    assert not {
        "create_file",
        "apply_patch",
        "start_deployment",
        "rollback_deployment",
    } & {row["tool_name"] for row in calls}
