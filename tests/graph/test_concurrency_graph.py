"""Coordinator-level concurrency tests (P1.F): real overlap + typed conflict.

Complements `tests/unit/test_concurrency.py` (which exercises `run_wave` /
`partition_wave` in isolation) by proving the wiring inside
`RunCoordinator._execute` end to end:

- two independent read-only tasks in the same wave genuinely overlap in wall
  clock time (not just "run without crashing"),
- two independent writer tasks that happen to produce colliding real patches
  (same file content) are still caught by the post-hoc lineage check and
  surfaced as a typed `composition_conflict` result rather than silently
  corrupting the composed patch.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import AppConfig, load_config
from product_factory.domain.budgets import RunBudget
from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.lifecycle import RunLifecycleEngine
from tests.conftest import clone_fixture


def _config() -> AppConfig:
    root = Path(__file__).resolve().parents[2]
    return load_config(root)


def _fixture(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[2]
    return clone_fixture(root / "tests" / "fixtures" / "sample_api", tmp_path / "repo")


def _new_coordinator(tmp_path: Path) -> RunCoordinator:
    return RunCoordinator(
        config=_config(),
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )


def _read_only_overlap_plan(request_text: str) -> PlannerOutput:
    """Two independent repository_analysis tasks + a linear implement/compose tail."""
    read_a = TaskSpec(
        id="T-001a",
        title="Inspect repository structure (A)",
        capability="repository_analysis",
        objective="Identify relevant modules and conventions (branch A)",
        expected_output_schema="repository_analysis.v1",
        required_tool_classes={"repository_read"},
        prohibited_actions={"file_write"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-001a", description="Relevant files identified", verification="evidence_check"
            )
        ],
    )
    read_b = read_a.model_copy(
        update={
            "id": "T-001b",
            "title": "Inspect repository structure (B)",
            "objective": "Identify relevant modules and conventions (branch B)",
            "acceptance_criteria": [
                AcceptanceCriterion(
                    id="AC-001b",
                    description="Relevant files identified",
                    verification="evidence_check",
                )
            ],
        }
    )
    implementation = TaskSpec(
        id="T-002",
        title="Implement change",
        capability="implementation",
        objective=request_text,
        expected_output_schema="implementation_result.v1",
        required_tool_classes={"repository_read", "repository_write", "git_read", "git_write"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-002", description="Change implemented with tests", verification="test_suite"
            )
        ],
        allowed_path_patterns=["**/*"],
        rationale="Justified broad path scope for fixture-wide code changes",
    )
    composition = TaskSpec(
        id="T-003",
        title="Compose patch",
        capability="composition",
        objective="Produce final proposed.patch",
        dependencies=["T-002"],
        expected_output_schema="composition_result.v1",
        required_tool_classes={"git_read", "artifact_write"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-003", description="Patch artifact produced", verification="artifact_check"
            )
        ],
    )
    return PlannerOutput(
        objective=request_text[:200],
        tasks=[read_a, read_b, implementation, composition],
        final_artifacts=[
            FinalArtifactSpec(logical_name="proposed.patch", composer_task_id="T-003")
        ],
        validation_strategy="deterministic behavioral validation",
        risk_classification="low",
    )


def test_two_read_only_tasks_overlap_in_the_same_wave(tmp_path: Path, monkeypatch) -> None:
    fixture = _fixture(tmp_path)
    coord = _new_coordinator(tmp_path)

    monkeypatch.setattr(
        RunLifecycleEngine,
        "_plan",
        lambda self, run_id, request, repo_summary=None, repair_errors=None, **kwargs: (
            _read_only_overlap_plan(request.request_text)
        ),
    )

    original_execute_task = RunLifecycleEngine._execute_task
    delay_s = 0.15

    def slow_execute_task(self, *, task, **kwargs):  # type: ignore[no-untyped-def]
        if task.capability == "repository_analysis":
            time.sleep(delay_s)
        return original_execute_task(self, task=task, **kwargs)

    monkeypatch.setattr(RunLifecycleEngine, "_execute_task", slow_execute_task)

    request = RunRequest(
        request_id="req-concurrency-overlap",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00"), max_parallel_tasks=3),
    )
    manifest = coord.run(request)
    assert manifest.final_status in {"completed", "awaiting_approval", "failed"}

    task_a = coord.db.get_task(manifest.run_id, "T-001a")
    task_b = coord.db.get_task(manifest.run_id, "T-001b")
    assert task_a is not None and task_b is not None
    assert task_a["status"] == "success"
    assert task_b["status"] == "success"

    from datetime import datetime

    ended_a = datetime.fromisoformat(task_a["ended_at"])
    ended_b = datetime.fromisoformat(task_b["ended_at"])
    gap = abs((ended_a - ended_b).total_seconds())
    # Each task sleeps `delay_s` before doing its (near-instant) real work. If
    # they ran serially the second task's ended_at would trail the first by
    # roughly `delay_s`; running concurrently keeps them close together.
    assert gap < delay_s * 0.6, f"expected concurrent overlap, got {gap:.3f}s gap"


def _variant_impl_files(request_text: str, *, task_objective: str = "") -> list[tuple[str, str]]:
    """Test-only stand-in for `deterministic_impl_files`: both branches write the
    *same path* but with *different content*, so the artifact store cannot
    dedupe the two patches by content hash — the collision must be caught by
    the real path-based lineage conflict check instead."""
    marker = "A" if "branch-a" in task_objective else "B"
    return [
        (
            "src/app/health.py",
            f'"""Health check module (variant {marker})."""\n\n'
            f"def health() -> dict[str, str]:\n"
            f'    return {{"status": "ok", "variant": "{marker}"}}\n',
        ),
        (
            "tests/test_health.py",
            "from app.health import health\n\n"
            f"def test_health_{marker.lower()}():\n"
            '    assert health()["status"] == "ok"\n',
        ),
    ]


def _writer_conflict_plan(request_text: str) -> PlannerOutput:
    """Two independent implementation tasks that deterministically write the
    same file path with different content, plus a composition task that must
    catch the real conflict when it inherits both patches."""
    impl_a = TaskSpec(
        id="IMPL-A",
        title="Implement health endpoint (branch A)",
        capability="implementation",
        objective="Add a validated health-check endpoint with tests. (branch-a)",
        expected_output_schema="implementation_result.v1",
        required_tool_classes={"repository_read", "repository_write", "git_read", "git_write"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-A", description="Change implemented with tests", verification="test_suite"
            )
        ],
        writable_path_patterns=["src/**", "tests/**"],
        rationale="Justified broad path scope for fixture-wide code changes",
    )
    impl_b = impl_a.model_copy(
        update={
            "id": "IMPL-B",
            "title": "Implement health endpoint (branch B)",
            "objective": "Add a validated health-check endpoint with tests. (branch-b)",
            "acceptance_criteria": [
                AcceptanceCriterion(
                    id="AC-B",
                    description="Change implemented with tests",
                    verification="test_suite",
                )
            ],
        }
    )
    composition = TaskSpec(
        id="T-COMP",
        title="Compose patch",
        capability="composition",
        objective="Produce final proposed.patch",
        dependencies=["IMPL-A", "IMPL-B"],
        expected_output_schema="composition_result.v1",
        required_tool_classes={"git_read", "artifact_write"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id="AC-COMP", description="Patch artifact produced", verification="artifact_check"
            )
        ],
    )
    return PlannerOutput(
        objective=request_text[:200],
        tasks=[impl_a, impl_b, composition],
        final_artifacts=[
            FinalArtifactSpec(logical_name="proposed.patch", composer_task_id="T-COMP")
        ],
        validation_strategy="deterministic behavioral validation",
        risk_classification="low",
    )


def test_conflicting_writers_yield_typed_composition_conflict(tmp_path: Path, monkeypatch) -> None:
    fixture = _fixture(tmp_path)
    coord = _new_coordinator(tmp_path)

    monkeypatch.setattr(
        RunLifecycleEngine,
        "_plan",
        lambda self, run_id, request, repo_summary=None, repair_errors=None, **kwargs: (
            _writer_conflict_plan(request.request_text)
        ),
    )
    monkeypatch.setattr(
        "product_factory.orchestration.lifecycle.engine.deterministic_impl_files",
        _variant_impl_files,
    )

    request = RunRequest(
        request_id="req-concurrency-conflict",
        workflow_type="code_change",
        request_text="Add a validated health-check endpoint with tests.",
        repository_path=fixture,
        budget=RunBudget(max_cost_usd=Decimal("3.00"), max_parallel_tasks=3),
    )
    manifest = coord.run(request)

    # Both writers succeed individually (isolated worktrees, no real race);
    # the collision is only observable — and must be caught — at composition.
    impl_a = coord.db.get_task(manifest.run_id, "IMPL-A")
    impl_b = coord.db.get_task(manifest.run_id, "IMPL-B")
    assert impl_a is not None
    assert impl_b is not None
    assert impl_a["status"] == "success"
    assert impl_b["status"] == "success"

    comp = coord.db.get_task(manifest.run_id, "T-COMP")
    assert comp is not None
    assert comp["status"] == "failed"

    import json

    result = json.loads(comp["result_json"])
    assert result["summary"] == "composition_conflict"
    validator_ids = {v["validator_id"] for v in result["validator_results"]}
    assert "composition_conflict" in validator_ids

    lineage_path = coord.pf_root / "runs" / manifest.run_id / "output" / "T-COMP-lineage.json"
    assert lineage_path.exists()
    lineage = json.loads(lineage_path.read_text())
    assert lineage["conflicts"], "expected recorded lineage conflicts"

    # The typed conflict feeds the existing repair loop (same as any other task
    # failure) rather than dead-ending the run: a repair task is spawned and
    # the run recovers instead of silently accepting a corrupted composed
    # patch. The original T-COMP attempt's typed failure is preserved as-is.
    all_tasks = {t["task_id"]: t for t in coord.db.list_tasks(manifest.run_id)}
    repair_ids = [tid for tid in all_tasks if tid.startswith("R-") and tid[2:].isdigit()]
    assert repair_ids, "expected a repair task spawned after the composition conflict"
    assert manifest.final_status in {"awaiting_approval", "completed", "failed"}
