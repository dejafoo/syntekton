"""Focused graph coverage for the confined technical-spike pack."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator


def test_mock_technical_spike_uses_data_dir_scratch_and_emits_result(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    repository = tmp_path / "repo"
    repository.mkdir()
    shutil.copy(
        project_root / "tests" / "fixtures" / "contracts" / "json_schema_valid.json",
        repository / "customer.json",
    )
    subprocess.run(["git", "init"], cwd=repository, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=repository,
        check=True,
        capture_output=True,
    )
    data_dir = tmp_path / ".product-factory"
    coordinator = RunCoordinator(
        config=load_config(project_root),
        gateway=MockGateway(),
        data_dir=data_dir,
        use_deterministic_planner=True,
    )
    manifest = coordinator.run(
        RunRequest(
            request_id="req-spike-1",
            workflow_type="technical_spike",
            request_text="Test whether the customer schema accepts a synthetic fixture.",
            repository_path=repository,
            pack_input={
                "hypothesis": "A minimal synthetic customer fixture validates.",
                "contract_paths": ["customer.json"],
            },
            approval_policy="none",
            metadata={"disable_review": "true", "planner_mode": "fixed"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    run_dir = data_dir / "runs" / manifest.run_id
    assert (run_dir / "scratch" / "T-001").is_dir()
    assert (run_dir / "scratch" / "T-001").resolve().is_relative_to(data_dir.resolve())
    payload = json.loads((run_dir / "output" / "SPIKE_RESULT.json").read_text(encoding="utf-8"))
    assert payload["hypothesis"] == "A minimal synthetic customer fixture validates."
    assert payload["method"]["mode"] == "local_synthetic"
    assert payload["measurements"]["simulation_status"] == "passed"
    assert {ref["schema_id"] for ref in payload["artifact_refs"]} >= {
        "contract_inventory.v1",
        "contract_compatibility.v1",
        "contract_simulation.v1",
    }
    assert payload["limits"]
    tool_calls = coordinator.db.list_tool_calls(manifest.run_id)
    assert {"contract_inventory", "generate_synthetic_fixture", "run_contract_simulation"} <= {
        row["tool_name"] for row in tool_calls
    }
