"""RF2 / RF3 — effective policy and artifact capture (ADR-007)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskBudget, TaskSpec
from product_factory.observability.contracts import CaptureLevel
from product_factory.observability.query import ObservabilityQueryService
from product_factory.orchestration.effective_policy import (
    EFFECTIVE_TASK_POLICY_SCHEMA,
    resolve_effective_task_policy,
)
from product_factory.persistence.artifact_policy import (
    ArtifactInstance,
    resolve_visibility,
    retain_body,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.schemas import default_schema_registry, validate_write_payload
from product_factory.tools.registry import default_tool_registry
from product_factory.validation.evidence import write_validation_evidence


def _implementation_task() -> TaskSpec:
    return TaskSpec(
        id="impl-1",
        title="Implement",
        capability="implementation",
        objective="ship a change",
        expected_output_schema="change_set.patch.v1",
        required_tool_classes=["filesystem_read", "filesystem_write", "git", "validation"],
        budget=TaskBudget(max_tool_calls=8, max_cost_usd="1.00"),
    )


def test_effective_task_policy_schema_round_trip() -> None:
    registry = default_schema_registry()
    assert registry.get(EFFECTIVE_TASK_POLICY_SCHEMA) is not None
    request = RunRequest(
        request_id="req-rf2",
        request_text="do the thing",
        workflow_type="code_change",
    )
    policy = resolve_effective_task_policy(
        run_id="run-rf2",
        task=_implementation_task(),
        request=request,
        tool_registry=default_tool_registry(),
        model_profile="mock-default",
        agent_profile="implementation_worker",
        skill_ids=["impl.core"],
        route_class="cloud",
        fallback_model_profile="cloud-fallback",
        fallback_eligible=True,
    )
    payload = policy.model_dump(mode="json")
    validate_write_payload(EFFECTIVE_TASK_POLICY_SCHEMA, payload)
    assert set(policy.prompt_tool_names) <= set(policy.allowed_tool_names)
    assert "apply_patch" in policy.allowed_tool_names
    assert policy.schema_version == EFFECTIVE_TASK_POLICY_SCHEMA


def test_prompt_tools_are_subset_of_allowed_grant() -> None:
    request = RunRequest(
        request_id="req-rf2b",
        request_text="implement",
        workflow_type="code_change",
    )
    policy = resolve_effective_task_policy(
        run_id="run-rf2b",
        task=_implementation_task(),
        request=request,
        tool_registry=default_tool_registry(),
        model_profile="mock-default",
        agent_profile="implementation_worker",
        skill_ids=[],
        prompt_tool_names=["read_file", "apply_patch", "web_search"],
        prompt_reduction_reason="unit_test_reduction",
    )
    assert "web_search" not in policy.prompt_tool_names
    assert set(policy.prompt_tool_names) <= set(policy.allowed_tool_names)
    assert "read_file" in policy.prompt_tool_names
    assert "apply_patch" in policy.allowed_tool_names
    assert policy.prompt_reduction_reason == "unit_test_reduction"


@pytest.mark.parametrize(
    ("content_class", "level", "expected"),
    [
        ("raw_validation_capture", CaptureLevel.OFF, "unavailable"),
        ("raw_validation_capture", CaptureLevel.METADATA, "metadata_only"),
        ("raw_validation_capture", CaptureLevel.FULL, "available"),
        ("normalized_evidence", CaptureLevel.METADATA, "metadata_only"),
        ("durable_output", CaptureLevel.FULL, "available"),
    ],
)
def test_capture_matrix_visibility(content_class: str, level: CaptureLevel, expected: str) -> None:
    assert resolve_visibility(content_class, level) == expected
    if expected in {"available", "redacted"}:
        assert retain_body(expected)  # type: ignore[arg-type]
    else:
        assert not retain_body(expected)  # type: ignore[arg-type]


def test_raw_validation_unavailable_under_metadata(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    db = Database(tmp_path / "db.sqlite")
    run_id = "run-rf3"
    db.upsert_run(
        run_id=run_id,
        workflow_type="code_change",
        status="running",
        request={"request_text": "x"},
    )
    evidence = write_validation_evidence(
        artifact_store=store,
        command_id="python_tests",
        registered_command_ids={"python_tests"},
        stdout="1 passed in 0.01s\n",
        stderr="",
        exit_code=0,
        input_revision="abc",
        created_by_task_id="task-1",
        run_id=run_id,
        capture_level=CaptureLevel.METADATA,
        on_instance=lambda inst: db.record_artifact_instance(inst.model_dump(mode="json")),
    )
    raw = json.loads(store.get_text(evidence.raw_ref.sha256))
    assert "stdout" not in raw
    assert raw.get("body_retained") is False
    # Report remains available under metadata while raw is withheld.
    report = json.loads(store.get_text(evidence.artifact_ref.sha256))
    assert report["command_id"] == "python_tests"

    run_dir = tmp_path / "runs" / run_id
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "blobs").symlink_to(store.blobs, target_is_directory=True)
    query = ObservabilityQueryService(db, data_dir=tmp_path)
    raw_view = query.artifact_content(run_id, evidence.raw_ref.sha256)
    assert raw_view is not None
    assert raw_view.available is False
    assert raw_view.reason == "metadata_only"
    assert raw_view.content_class == "raw_validation_capture"

    report_view = query.artifact_content(run_id, evidence.artifact_ref.sha256)
    assert report_view is not None
    assert report_view.available is True
    assert report_view.content_class == "normalized_evidence"


def test_raw_validation_off_has_no_recoverable_body(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    evidence = write_validation_evidence(
        artifact_store=store,
        command_id="python_tests",
        registered_command_ids={"python_tests"},
        stdout="secret stdout",
        stderr="secret stderr",
        exit_code=1,
        input_revision="abc",
        created_by_task_id="task-1",
        run_id="run-off",
        capture_level=CaptureLevel.OFF,
    )
    assert not store.exists(evidence.raw_ref.sha256)
    assert evidence.raw_instance is not None
    assert evidence.raw_instance.visibility == "unavailable"


def test_cross_run_artifact_hash_returns_none(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    db = Database(tmp_path / "db.sqlite")
    for run_id in ("run-a", "run-b"):
        db.upsert_run(
            run_id=run_id,
            workflow_type="code_change",
            status="completed",
            request={"request_text": "x"},
        )
    evidence = write_validation_evidence(
        artifact_store=store,
        command_id="python_tests",
        registered_command_ids={"python_tests"},
        stdout="ok\n",
        stderr="",
        exit_code=0,
        input_revision="abc",
        created_by_task_id="task-1",
        run_id="run-a",
        capture_level=CaptureLevel.FULL,
        on_instance=lambda inst: db.record_artifact_instance(inst.model_dump(mode="json")),
    )
    run_dir = tmp_path / "runs" / "run-a"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "artifacts" / "blobs").symlink_to(store.blobs, target_is_directory=True)
    query = ObservabilityQueryService(db, data_dir=tmp_path)
    assert query.artifact_content("run-a", evidence.raw_ref.sha256) is not None
    assert query.artifact_content("run-b", evidence.raw_ref.sha256) is None
    assert query.artifact_content("run-a", "0" * 64) is None


def test_artifact_instance_create_sets_visibility() -> None:
    instance = ArtifactInstance.create(
        run_id="run-1",
        sha256="a" * 64,
        content_class="raw_tool_capture",
        capture_level=CaptureLevel.OFF,
        role="tool-stdout",
    )
    assert instance.visibility == "unavailable"
    assert not retain_body(instance.visibility)
