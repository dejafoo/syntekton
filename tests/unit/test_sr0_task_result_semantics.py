"""SR0 task-result semantics: characterization and quality deadlock regression."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from product_factory.domain.artifacts import ArtifactRef
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.plans import CompiledPlan
from product_factory.domain.tasks import (
    AcceptanceCriterion,
    TaskResult,
    TaskSpec,
    is_terminal_task_status,
    requires_repair_or_terminal_resolution,
    satisfies_dependency,
)
from product_factory.executors.protocol import TaskExecutionRequest
from product_factory.executors.validation import TestExecutionExecutor
from product_factory.orchestration.effective_policy import EffectiveTaskPolicy
from product_factory.registry.capability_descriptors import require_descriptor
from product_factory.scheduling.scheduler import runnable_tasks

STATUS_CASES = (
    ("success", True, True, False),
    ("skipped", True, True, False),
    ("partial", True, False, True),
    ("blocked", True, False, True),
    ("failed", True, False, True),
    ("budget_exhausted", True, False, True),
    ("unsupported", True, False, True),
    ("pending", False, False, False),
    ("running", False, False, False),
)


@pytest.mark.parametrize(
    ("status", "terminal", "satisfies", "needs_resolution"),
    STATUS_CASES,
)
def test_task_result_status_predicates(
    status: str,
    terminal: bool,
    satisfies: bool,
    needs_resolution: bool,
) -> None:
    assert is_terminal_task_status(status) is terminal
    assert satisfies_dependency(status) is satisfies
    assert requires_repair_or_terminal_resolution(status) is needs_resolution


def test_partial_cannot_satisfy_dependency() -> None:
    assert not satisfies_dependency("partial")


def test_task_result_accepts_skipped_status() -> None:
    result = TaskResult(task_id="T-1", status="skipped", summary="superseded by repair")
    assert result.status == "skipped"


def _task(task_id: str, *, deps: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        title=task_id,
        capability="test_execution",
        objective="run tests",
        rationale="r",
        dependencies=deps or [],
        expected_output_schema="validation_receipt.v1",
        required_tool_classes={"run_validation_command"},
        acceptance_criteria=[
            AcceptanceCriterion(
                id=f"{task_id}-ac",
                description="done",
                verification="test_suite",
                severity="blocking",
            )
        ],
        budget=TaskBudget(),
    )


def _plan(tasks: dict[str, TaskSpec]) -> CompiledPlan:
    return CompiledPlan(
        objective="quality",
        assumptions=[],
        tasks=tasks,
        task_order=list(tasks),
        final_artifacts=[],
        validation_strategy="registered_commands",
        risk_classification="low",
        request_acceptance_criteria=[],
    )


def test_scheduler_partial_blocks_dependents_characterization() -> None:
    """Pre-fix RF-01 shape: partial does not unlock dependents."""
    tasks = {
        "T-002": _task("T-002"),
        "T-004": _task("T-004", deps=["T-002"]),
    }
    ready = runnable_tasks(
        _plan(tasks),
        {"T-002": "partial", "T-004": "pending"},
        max_parallel=2,
    )
    assert ready == []


def test_scheduler_success_unlocks_dependents() -> None:
    tasks = {
        "T-002": _task("T-002"),
        "T-004": _task("T-004", deps=["T-002"]),
    }
    ready = runnable_tasks(
        _plan(tasks),
        {"T-002": "success", "T-004": "pending"},
        max_parallel=2,
    )
    assert [task.id for task in ready] == ["T-004"]


@dataclass
class _FakeBroker:
    responses: dict[str, Any] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)

    def execute(self, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        command_id = str(arguments.get("command_id") or "")
        if command_id in self.errors:
            raise self.errors[command_id]
        response = self.responses.get(command_id)
        if response is None:
            raise KeyError(command_id)
        return dict(response)


@dataclass
class _FakeArtifacts:
    payloads: list[dict[str, Any]] = field(default_factory=list)

    def put_json(self, payload: dict[str, Any], **kwargs: Any) -> ArtifactRef:
        self.payloads.append(payload)
        digest = f"{len(self.payloads):064d}"
        return ArtifactRef(
            sha256=digest,
            media_type="application/json",
            size_bytes=len(str(payload)),
            logical_name=str(kwargs.get("logical_name") or "validation.json"),
            relative_path=f"artifacts/{digest}.json",
            created_by_task_id=str(kwargs.get("created_by_task_id") or "T-002"),
            schema_id="validation_receipt.v1",
        )


def _request(
    *,
    broker: _FakeBroker,
    artifacts: _FakeArtifacts,
    command_ids: list[str] | None = None,
    granted: set[str] | None = None,
) -> TaskExecutionRequest:
    task = _task("T-002")
    descriptor = require_descriptor("test_execution")
    policy = EffectiveTaskPolicy(
        task_id=task.id,
        run_id="run-1",
        capability="test_execution",
        executor_mode="validation",
        allowed_tool_names=["run_validation_command"],
        allowed_tool_classes=["run_validation_command"],
        primary_model_profile="coding_worker",
        repair_eligible=False,
    )
    return TaskExecutionRequest(
        run_id="run-1",
        run_dir=Path("/tmp/run-1"),
        request=MagicMock(),
        task=task,
        effective_policy=policy,
        descriptor=descriptor,
        agent_profile="coding_worker",
        model_profile="coding_worker",
        broker=broker,
        artifacts=artifacts,
        gateway=MagicMock(),
        raw_gateway=MagicMock(),
        tool_registry=MagicMock(),
        allow_deterministic_workers=True,
        ctx_messages=[],
        package_hash="hash",
        granted_tool_names=granted if granted is not None else {"run_validation_command"},
        registered_command_ids=["unit"] if command_ids is None else command_ids,
    )


def test_test_execution_all_commands_pass() -> None:
    broker = _FakeBroker(
        responses={
            "unit": {
                "tool_call_id": "tc-1",
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
            }
        }
    )
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "success"
    assert result.findings == []
    assert artifacts.payloads[0]["domain_failures"] == 0


def test_test_execution_complete_failing_receipts_are_success_with_evidence() -> None:
    """RF-01 fix: complete failing receipts are successful executor completion."""
    broker = _FakeBroker(
        responses={
            "unit": {
                "tool_call_id": "tc-1",
                "exit_code": 1,
                "stdout": "FAILED",
                "stderr": "assertion",
                "validation_evidence_ref": "ev-1",
            }
        }
    )
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "success"
    assert result.validator_results
    assert result.validator_results[0].status == "fail"
    assert result.findings
    assert result.findings[0].severity == "blocking"
    assert artifacts.payloads[0]["domain_failures"] == 1
    assert artifacts.payloads[0]["synthesized_pass"] is False


def test_test_execution_timeout_with_complete_receipt_is_success() -> None:
    broker = _FakeBroker(
        responses={
            "unit": {
                "tool_call_id": "tc-1",
                "exit_code": 124,
                "stdout": "",
                "stderr": "Command timed out after 300s",
            }
        }
    )
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "success"
    assert result.validator_results[0].details["timed_out"] is True
    assert artifacts.payloads[0]["receipts"][0]["timed_out"] is True


def test_test_execution_missing_grant_is_blocked() -> None:
    broker = _FakeBroker()
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(
        _request(broker=broker, artifacts=artifacts, granted=set())
    )
    assert result.status == "blocked"


def test_test_execution_no_commands_is_blocked() -> None:
    broker = _FakeBroker()
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(
        _request(broker=broker, artifacts=artifacts, command_ids=[])
    )
    assert result.status == "blocked"


def test_test_execution_unavailable_executable_is_failed() -> None:
    broker = _FakeBroker(errors={"unit": FileNotFoundError("pytest")})
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "failed"
    assert "could not start or observe" in result.summary


def test_test_execution_authorization_error_is_blocked() -> None:
    broker = _FakeBroker(errors={"unit": ToolAuthorizationError("denied")})
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "blocked"


def test_test_execution_incomplete_receipt_is_failed() -> None:
    broker = _FakeBroker(
        responses={
            "unit": {
                "tool_call_id": "tc-1",
                "stdout": "truncated",
                "stderr": "",
            }
        }
    )
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "failed"
    assert "mandatory receipt incomplete" in result.summary


def test_failing_test_receipt_unlocks_quality_dependents() -> None:
    """Quality DAG must not deadlock when tests fail with complete receipts."""
    broker = _FakeBroker(
        responses={
            "unit": {
                "tool_call_id": "tc-1",
                "exit_code": 1,
                "stdout": "FAILED",
                "stderr": "boom",
            }
        }
    )
    artifacts = _FakeArtifacts()
    result = TestExecutionExecutor().execute(_request(broker=broker, artifacts=artifacts))
    assert result.status == "success"

    tasks = {
        "T-002": _task("T-002"),
        "T-004": _task("T-004", deps=["T-002"]),
        "T-008": _task("T-008", deps=["T-002", "T-004"]),
    }
    ready = runnable_tasks(
        _plan(tasks),
        {"T-002": result.status, "T-004": "pending", "T-008": "pending"},
        max_parallel=4,
    )
    assert [task.id for task in ready] == ["T-004"]
