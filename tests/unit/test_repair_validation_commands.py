"""Regression tests for repair validation-command guidance (bench failure class)."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.findings import ValidatorResult
from product_factory.domain.tools import CapabilityGrant
from product_factory.orchestration.repair import (
    behavioral_command_id,
    create_repair_tasks,
    repair_objective_for_failure,
    truncate_failure_details,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry


def test_behavioral_command_id_parsed() -> None:
    assert behavioral_command_id("behavioral:python_tests") == "python_tests"
    assert behavioral_command_id("patch_applies") is None


def test_repair_objective_teaches_registered_command_id_not_validator_label() -> None:
    fail = ValidatorResult(
        validator_id="behavioral:python_tests",
        status="fail",
        message="Behavioral command failed",
        details={"stdout": "FAILED test_x\n" * 500, "exit_code": 1},
    )
    objective = repair_objective_for_failure(
        fail, registered_command_ids=["python_tests", "python_typecheck"]
    )
    assert "command_id='python_tests'" in objective
    assert "Registered command ids only" in objective
    assert "behavioral:python_tests" in objective  # explained as forbidden
    assert "Do NOT pass the validator id" in objective
    # Huge pytest logs must be truncated so repair does not burn input tokens.
    assert len(objective) < 8_000
    assert "<truncated" in objective


def test_create_repair_tasks_title_avoids_raw_validator_id() -> None:
    fail = ValidatorResult(
        validator_id="behavioral:python_tests",
        status="fail",
        message="boom",
        details={},
    )
    repairs = create_repair_tasks(
        failures=[fail],
        findings=[],
        originating_task_id="T-001",
        allowed_path_patterns=["**/*"],
        registered_command_ids=["python_tests"],
    )
    assert len(repairs) == 1
    assert repairs[0].title == "Repair: behavioral (python_tests)"
    assert "command_id='python_tests'" in repairs[0].objective


def test_truncate_failure_details_keeps_tail() -> None:
    details = truncate_failure_details({"stdout": "a" * 10_000, "exit_code": 1})
    assert details["exit_code"] == 1
    assert len(details["stdout"]) < 10_000
    assert details["stdout"].endswith("a" * 100)


def test_broker_accepts_behavioral_prefix_and_pytest_alias(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        worktree_root=wt,
        registered_commands={
            "python_tests": {
                "executable": "python",
                "args": ["-c", "print('ok')"],
                "timeout_seconds": 5,
            }
        },
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            agent_profile="implementation_worker",
            tool_names={"run_validation_command"},
            allowed_path_patterns=["**/*"],
            max_calls=10,
        )
    )
    for command_id in ("python_tests", "behavioral:python_tests", "pytest"):
        out = broker.execute(
            task_id="t1",
            tool_name="run_validation_command",
            arguments={"command_id": command_id},
        )
        assert out["command_id"] == "python_tests"
        assert out["exit_code"] == 0


def test_broker_still_rejects_unknown_after_normalize(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
        worktree_root=wt,
        registered_commands={
            "echo_ok": {"executable": "echo", "args": ["ok"], "timeout_seconds": 5}
        },
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            agent_profile="implementation_worker",
            tool_names={"run_validation_command"},
            allowed_path_patterns=["**/*"],
            max_calls=5,
        )
    )
    with pytest.raises(ToolAuthorizationError, match="registered ids"):
        broker.execute(
            task_id="t1",
            tool_name="run_validation_command",
            arguments={"command_id": "behavioral:not_a_real_command"},
        )


def test_normalize_validation_command_id_helpers() -> None:
    assert (
        ToolBroker.normalize_validation_command_id("behavioral:python_tests")
        == "python_tests"
    )
    assert ToolBroker.normalize_validation_command_id("pytest") == "python_tests"
    assert ToolBroker.normalize_validation_command_id("python_typecheck") == "python_typecheck"
