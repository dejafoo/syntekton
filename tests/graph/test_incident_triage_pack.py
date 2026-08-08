from __future__ import annotations

import json
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.connectors.policy import ConnectorSettings
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_mock_incident_triage_emits_read_only_operational_record(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "tests/fixtures/ops/incident_known.json").read_text(encoding="utf-8")
    )
    data_dir = tmp_path / ".product-factory"
    config = load_config(root)
    settings = dict(config.connectors.connectors)
    settings["ops_read"] = ConnectorSettings(enabled=True)
    config = config.model_copy(
        update={"connectors": config.connectors.model_copy(update={"connectors": settings})}
    )
    coordinator = RunCoordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=data_dir,
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="req-incident-graph",
            workflow_type="incident_triage",
            request_text="Triage incident INC-42.",
            pack_input=fixture,
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    payload = json.loads(
        (data_dir / "runs" / manifest.run_id / "output" / "OPERATIONAL_RECORD.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["follow_up"] == "rollback_decision"
    assert payload["authority"]["deploy"] is False
    calls = {row["tool_name"] for row in coordinator.db.list_tool_calls(manifest.run_id)}
    assert "query_service_signals" in calls
    assert not calls & {
        "create_file",
        "apply_patch",
        "start_deployment",
        "rollback_deployment",
        "restart_service",
        "shift_traffic",
    }
