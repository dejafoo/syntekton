"""RF6 observability projections: policy, route, visibility, next action."""

from __future__ import annotations

import hashlib
from pathlib import Path

from product_factory.observability.contracts import CaptureLevel
from product_factory.observability.operator import operator_next_action
from product_factory.observability.query import ObservabilityQueryService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.database import Database


def test_operator_next_action_for_approval_and_blocked() -> None:
    assert "approve" in (operator_next_action(run_status="awaiting_approval") or "").lower()
    assert (
        "validation"
        in (
            operator_next_action(
                run_status="failed",
                task_status="blocked",
                has_validation_failures=True,
            )
            or ""
        ).lower()
    )


def test_task_policy_and_invocation_route_projections(tmp_path: Path) -> None:
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "runs" / "run-rf6").mkdir(parents=True)
    db = Database(data_dir / "data" / "product_factory.sqlite")
    db.upsert_run(
        run_id="run-rf6",
        workflow_type="repository_change",
        status="awaiting_approval",
        request={"budget": {"max_cost_usd": "1"}},
    )
    db.upsert_task(
        run_id="run-rf6",
        task_id="impl",
        capability="implementation",
        status="success",
        spec={"title": "Implement", "dependencies": []},
        result={"model_profile": "coding_worker", "summary": "done"},
        effective_policy={
            "schema_version": "effective_task_policy.v1",
            "task_id": "impl",
            "run_id": "run-rf6",
            "capability": "implementation",
            "allowed_tool_names": ["create_file", "apply_patch"],
            "prompt_tool_names": ["create_file"],
            "primary_model_profile": "coding_worker",
            "route_class": "local",
            "fallback_model_profile": "coding_worker_cloud",
            "fallback_eligible": True,
            "stack_profile_digest": "abc123",
            "approval_required": True,
        },
    )
    db.record_invocation(
        request_id="req-1",
        run_id="run-rf6",
        task_id="impl",
        model_profile="coding_worker",
        status="success",
        usage={"estimated_cost_usd": "0.01", "input_tokens": 10, "output_tokens": 5},
        response_hash="deadbeef",
        provider="openai_compatible",
        resolved_model_id="local/model",
        routing={
            "route": "cloud",
            "primary_profile": "coding_worker",
            "fallback_profile": "coding_worker_cloud",
            "fallback_reason": "local_unhealthy",
            "provider": "openrouter",
            "model": "cloud/model",
            "cost_basis": "estimated",
            "cost_usd": "0.02",
        },
    )
    body = b"legacy bytes"
    sha = hashlib.sha256(body).hexdigest()
    blob_dir = data_dir / "runs" / "run-rf6" / "artifacts" / "blobs"
    blob_dir.mkdir(parents=True)
    (blob_dir / sha).write_bytes(body)
    db.record_artifact(
        {
            "sha256": sha,
            "media_type": "text/plain",
            "size_bytes": len(body),
            "logical_name": "out.txt",
            "relative_path": f"blobs/{sha}",
            "created_by_task_id": "impl",
            "trust_level": "generated",
        }
    )
    db.record_artifact_instance(
        ArtifactInstance.create(
            run_id="run-rf6",
            sha256=sha,
            content_class="durable_output",
            capture_level=CaptureLevel.REDACTED,
            role="patch",
            producer_task_id="impl",
            media_type="text/plain",
            size_bytes=len(body),
            display_name="out.txt",
        ).model_dump(mode="json")
    )

    query = ObservabilityQueryService(db, data_dir=data_dir)
    run = query.get_run("run-rf6")
    assert run is not None
    assert run.next_action and "approve" in run.next_action.lower()

    task = query.get_task("run-rf6", "impl")
    assert task is not None
    assert task.legacy_policy is False
    assert task.route_class == "local"
    assert task.fallback_model_profile == "coding_worker_cloud"
    assert task.effective_policy is not None
    assert task.effective_policy["allowed_tool_names"] == ["create_file", "apply_patch"]
    assert task.stack_profile_digest == "abc123"

    invocation = query.list_invocations("run-rf6")[0]
    assert invocation.route == "cloud"
    assert invocation.fallback_reason == "local_unhealthy"
    assert invocation.fallback_profile == "coding_worker_cloud"
    assert invocation.cost_usd == "0.02"

    costs = query.costs("run-rf6")
    assert any(row.get("route") == "cloud" for row in costs.by_route)

    artifact = query.list_artifacts_for_run("run-rf6")[0]
    assert artifact.visibility == "redacted"
    assert artifact.content_class == "durable_output"
    assert artifact.producer_role == "patch"
    assert artifact.legacy is False

    content = query.artifact_content("run-rf6", sha)
    assert content is not None
    assert content.available is True
    assert content.redacted is True
    assert content.visibility == "redacted"
    db.close()


def test_legacy_task_without_policy_is_marked(tmp_path: Path) -> None:
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    db = Database(data_dir / "data" / "product_factory.sqlite")
    db.upsert_run(
        run_id="legacy",
        workflow_type="code_change",
        status="succeeded",
        request={},
    )
    db.upsert_task(
        run_id="legacy",
        task_id="t0",
        capability="implementation",
        status="success",
        spec={"title": "old"},
    )
    query = ObservabilityQueryService(db, data_dir=data_dir)
    task = query.get_task("legacy", "t0")
    assert task is not None
    assert task.legacy_policy is True
    assert task.effective_policy is None
    db.close()
