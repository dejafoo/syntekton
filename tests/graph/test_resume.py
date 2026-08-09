"""Durable resume tests (P1.B): crash mid-task, resume, skip completed work."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.application import build_coordinator
from product_factory.config.loader import AppConfig, load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.errors import ApprovalBlockedError, RuntimeFailureError
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.wave_execution import WaveExecutionService
from tests.conftest import clone_fixture


def _config() -> AppConfig:
    root = Path(__file__).resolve().parents[2]
    return load_config(root)


def _fixture(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    return clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def _new_coordinator(tmp_path: Path) -> RunCoordinator:
    return build_coordinator(
        config=_config(),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def test_resume_skips_completed_tasks_and_retries_crashed_task(tmp_path: Path, monkeypatch) -> None:
    fixture = _fixture(tmp_path)
    coord1 = _new_coordinator(tmp_path)

    original_execute_task = WaveExecutionService.execute_task

    def crashing_execute_task(self, *, task, **kwargs):  # type: ignore[no-untyped-def]
        if task.id == "T-004":
            raise RuntimeError("simulated crash mid-task")
        return original_execute_task(self, task=task, **kwargs)

    monkeypatch.setattr(WaveExecutionService, "execute_task", crashing_execute_task)

    request = RunRequest(
        request_id="req-resume-1",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00")),
    )
    with pytest.raises(RuntimeFailureError):
        coord1.run(request)

    rows = coord1.queries.database.list_runs()
    assert len(rows) == 1
    run_id = rows[0]["run_id"]

    # The exception handler in `run()` marks the row "failed"; overwrite it to
    # "executing" to faithfully simulate a process that crashed before it
    # could persist any failure status at all.
    row = coord1.queries.database.get_run(run_id)
    assert row is not None
    import json

    coord1.queries.database.upsert_run(
        run_id=run_id,
        workflow_type=row["workflow_type"],
        status="executing",
        request=json.loads(row["request_json"]),
        base_commit=row.get("base_commit"),
        active_operation="task:T-004",
    )

    # T-002 (implementation) succeeded before the crash; confirm it is
    # persisted and has recorded tool calls prior to resume.
    t002_before = coord1.queries.database.get_task(run_id, "T-002")
    assert t002_before is not None
    assert t002_before["status"] == "success"
    tool_calls_before = coord1.queries.database.conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE run_id = ? AND task_id = ?", (run_id, "T-002")
    ).fetchone()[0]
    assert tool_calls_before > 0

    # T-004 (composition) never got a worktree or result — a true crash before
    # any of its own side effects.
    t004_before = coord1.queries.database.get_task(run_id, "T-004")
    assert t004_before is not None
    assert t004_before["status"] == "running"
    assert t004_before.get("result_json") is None

    # Simulate a process restart: fresh coordinator instance, same data dir.
    coord2 = _new_coordinator(tmp_path)
    invoked_task_ids: list[str] = []

    def tracking_execute_task(self, *, task, **kwargs):  # type: ignore[no-untyped-def]
        invoked_task_ids.append(task.id)
        return original_execute_task(self, task=task, **kwargs)

    monkeypatch.setattr(WaveExecutionService, "execute_task", tracking_execute_task)

    manifest = coord2.resume(run_id)

    # The crashed patch composer and its not-yet-started ChangeSet dependent are
    # dispatched; the previously completed implementation incurs no new spend.
    assert invoked_task_ids == ["T-004", "T-005"]
    assert manifest.final_status in {"completed", "awaiting_approval"}

    tool_calls_after = coord2.queries.database.conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE run_id = ? AND task_id = ?", (run_id, "T-002")
    ).fetchone()[0]
    assert tool_calls_after == tool_calls_before

    t004_after = coord2.queries.database.get_task(run_id, "T-004")
    assert t004_after is not None
    assert t004_after["status"] == "success"
    assert int(t004_after["attempt"]) == 2


def test_resume_unknown_run_id_fails_closed(tmp_path: Path) -> None:
    coord = _new_coordinator(tmp_path)
    from product_factory.domain.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        coord.resume("run-does-not-exist")


def test_resume_blocks_legacy_effective_policy_before_execution(tmp_path: Path) -> None:
    """A pre-v2 persisted grant never reaches a gateway or tool on resume."""
    from product_factory.domain.errors import ConfigurationError

    coord = _new_coordinator(tmp_path)
    request = RunRequest(
        request_id="legacy-policy",
        workflow_type="code_change",
        request_text="resume legacy work",
    )
    run_id = "run-legacy-policy"
    (tmp_path / ".product-factory" / "runs" / run_id).mkdir(parents=True)
    coord.queries.database.upsert_run(
        run_id=run_id,
        workflow_type=request.workflow_type,
        status="executing",
        request=request.model_dump(mode="json"),
    )
    coord.queries.database.upsert_task(
        run_id=run_id,
        task_id="T-001",
        capability="implementation",
        status="running",
        spec={"id": "T-001", "dependencies": []},
        effective_policy={
            "schema_version": "effective_task_policy.v1",
            "task_id": "T-001",
            "run_id": run_id,
            "capability": "implementation",
        },
    )

    with pytest.raises(ConfigurationError, match="restart_required"):
        coord.resume(run_id)

    row = coord.queries.database.get_run(run_id)
    assert row is not None
    assert row["status"] == "blocked"
    assert row["active_operation"] == "restart_required"
    assert coord.queries.database.list_events(run_id=run_id, types=["run.policy_incompatible"])


def test_resume_rejects_already_terminal_run(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    coord = _new_coordinator(tmp_path)
    manifest = coord.run(
        RunRequest(
            request_id="req-resume-terminal",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        )
    )
    assert manifest.final_status == "awaiting_approval"
    from product_factory.domain.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        coord.resume(manifest.run_id)


def test_approval_path_works_across_restart(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    coord1 = _new_coordinator(tmp_path)
    manifest = coord1.run(
        RunRequest(
            request_id="req-resume-approval",
            workflow_type="code_change",
            request_text="Add a validated health-check endpoint with tests.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
        )
    )
    assert manifest.final_status == "awaiting_approval"

    # Simulate a process restart: brand-new coordinator/db handle, same data dir.
    coord2 = _new_coordinator(tmp_path)
    with pytest.raises(ApprovalBlockedError):
        coord2.apply_patch(manifest.run_id)
    result = coord2.approve(manifest.run_id, apply=False)
    assert result["status"] == "approved"
    row = coord2.queries.database.get_run(manifest.run_id)
    assert row is not None
    assert row["status"] == "completed"
