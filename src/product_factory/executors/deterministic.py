"""Deterministic executors: repository inventory and deployment state machine."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from product_factory.domain.errors import ApprovalBlockedError
from product_factory.domain.tasks import TaskResult
from product_factory.domain.usage import UsageMetrics
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
    blocked_result,
)


class RepositoryInventoryExecutor:
    executor_mode = "deterministic"
    adapter_ids = frozenset({"repository_inventory"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        tool_call_ids: list[str] = []
        if not broker.worktree_root:
            # Optional analysis (e.g. change_intake without a repository) stays
            # honest: empty inventory receipt, not a success-shaped stub.
            if not request.request.repository_path:
                art = artifacts.put_json(
                    {
                        "files": [],
                        "languages": [],
                        "entry_points": [],
                        "tests": [],
                        "configuration": [],
                        "relevant_excerpts": request.repository_excerpts,
                        "conventions": "No repository attached; inventory skipped",
                    },
                    logical_name="repository-analysis.json",
                    created_by_task_id=task.id,
                )
                return attach_receipt(
                    TaskResult(
                        task_id=task.id,
                        status="success",
                        summary="Repository inventory skipped (no repository)",
                        artifact_refs=[art],
                        model_profile=request.model_profile,
                        resolved_model_id=request.model_profile,
                        prompt_package_hash=request.package_hash,
                    ),
                    request=request,
                    execution_mode=(
                        "deterministic_mock"
                        if request.allow_deterministic_workers
                        else "live"
                    ),
                    activity={"reason": "no_repository", "parser": request.descriptor.parser_id},
                )
            return blocked_result(
                request,
                summary="repository_inventory requires a worktree",
                activity={"reason": "missing_worktree"},
            )
        listing = broker.execute(
            task_id=task.id,
            tool_name="list_files",
            arguments={"directory": ".", "glob": "**/*"},
        )
        tool_call_ids.append(listing["tool_call_id"])
        listed_paths = [
            str(entry.get("path", "")) if isinstance(entry, dict) else str(entry)
            for entry in listing.get("files", [])
        ]
        report = {
            "files": listed_paths[:50],
            "languages": sorted(
                {Path(path).suffix.lstrip(".") for path in listed_paths if Path(path).suffix}
            ),
            "entry_points": [
                path
                for path in listed_paths
                if Path(path).name in {"main.py", "app.py", "cli.py", "index.ts", "package.json"}
            ][:20],
            "tests": [path for path in listed_paths if "test" in Path(path).name.lower()][:20],
            "configuration": [
                path
                for path in listed_paths
                if Path(path).name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
            ][:20],
            "relevant_excerpts": request.repository_excerpts,
            "conventions": "Derived from repository paths and targeted excerpts",
        }
        art = artifacts.put_json(
            report, logical_name="repository-analysis.json", created_by_task_id=task.id
        )
        shutil.copy(
            artifacts.blobs / art.sha256,
            request.run_dir / "output" / "repository-analysis.json",
        )
        execution_mode = (
            "deterministic_mock" if request.allow_deterministic_workers else "live"
        )
        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status="success",
                summary="Repository analyzed",
                artifact_refs=[art],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
                usage=UsageMetrics(),
            ),
            request=request,
            execution_mode=execution_mode,
            activity={
                "tools": ["list_files"],
                "file_count": len(listed_paths),
                "parser": request.descriptor.parser_id,
            },
        )


class DeploymentStateMachineExecutor:
    executor_mode = "deterministic"
    adapter_ids = frozenset({"deployment_state_machine"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        tool_call_ids: list[str] = []
        if not broker.connector_approval_verified:
            raise ApprovalBlockedError(
                "Deployment approval binding is missing or does not match immutable inputs"
            )
        data = request.request.pack_input
        actions: list[dict[str, Any]] = []

        def _connector_payload(value: dict[str, Any]) -> dict[str, Any]:
            payload = value.get("result")
            return dict(payload) if isinstance(payload, dict) else dict(value)

        try:
            resolved_call = broker.execute(
                task_id=task.id,
                tool_name="resolve_deployment_target",
                arguments={"target_id": data["target_id"]},
            )
            tool_call_ids.append(resolved_call["tool_call_id"])
            resolved = _connector_payload(resolved_call)
            actions.append(resolved)
            started_call = broker.execute(
                task_id=task.id,
                tool_name="start_deployment",
                arguments={
                    "target_id": data["target_id"],
                    "release_plan_digest": data["release_plan_digest"],
                    "artifact_digest": data["artifact_digest"],
                    "idempotency_key": data["idempotency_key"],
                    "change_window": data["change_window"],
                },
            )
            tool_call_ids.append(started_call["tool_call_id"])
            started = _connector_payload(started_call)
            actions.append(started)
            deployment_id = str(started.get("deployment_id") or "")
            status_call = broker.execute(
                task_id=task.id,
                tool_name="get_rollout_status",
                arguments={"deployment_id": deployment_id},
            )
            tool_call_ids.append(status_call["tool_call_id"])
            actions.append(_connector_payload(status_call))
            health_args: dict[str, Any] = {"deployment_id": deployment_id}
            if "simulated_health" in data:
                health_args["healthy"] = bool(data["simulated_health"])
            if data.get("health_checks"):
                health_args["checks"] = data["health_checks"]
            health_call = broker.execute(
                task_id=task.id,
                tool_name="verify_health",
                arguments=health_args,
            )
            tool_call_ids.append(health_call["tool_call_id"])
            health = _connector_payload(health_call)
            actions.append(health)
            healthy = bool(
                health.get("healthy")
                if "healthy" in health
                else health.get("status") == "succeeded"
            )
            data["environment"] = str(resolved.get("environment") or "staging")
            data["health_checks"] = list(
                health.get("checks")
                or (health.get("health") or {}).get("checks")
                or [{"name": "rollout", "passed": healthy, "healthy": healthy}]
            )
            if not healthy:
                rollback_call = broker.execute(
                    task_id=task.id,
                    tool_name="rollback_deployment",
                    arguments={"deployment_id": deployment_id},
                )
                tool_call_ids.append(rollback_call["tool_call_id"])
                rollback = _connector_payload(rollback_call)
                actions.append(rollback)
                data["rollback_result"] = dict(rollback)
                data["deployment_outcome"] = "rolled_back"
            else:
                data["deployment_outcome"] = "succeeded"
            data["action_log"] = actions
            data["reconciliation"] = {
                "idempotency_key": data["idempotency_key"],
                "replayed": bool(started.get("replayed")),
                "deployment_id": deployment_id,
            }
            receipt_artifact = artifacts.put_json(
                {
                    "schema_id": "deployment_execution_receipts.v1",
                    "actions": actions,
                    "outcome": data["deployment_outcome"],
                    "reconciliation": data["reconciliation"],
                },
                logical_name="deployment-receipts.json",
                created_by_task_id=task.id,
            )
            summary = f"Deployment {data['deployment_outcome']}"
            artifact_refs = [receipt_artifact]
        except ApprovalBlockedError:
            raise
        except Exception as exc:
            data["deployment_outcome"] = "unknown"
            data["action_log"] = actions + [
                {
                    "action": "deployment_execution",
                    "status": "unknown",
                    "reason": type(exc).__name__,
                    "durable": True,
                    "reconciliation_required": True,
                }
            ]
            data["health_checks"] = []
            data["rollback_result"] = {
                "status": "not_attempted",
                "reason": "deployment state unknown; reconcile before another effect",
            }
            artifact_refs = [
                artifacts.put_json(
                    {
                        "schema_id": "deployment_execution_receipts.v1",
                        "actions": data["action_log"],
                        "outcome": "unknown",
                        "error_type": type(exc).__name__,
                    },
                    logical_name="deployment-receipts.json",
                    created_by_task_id=task.id,
                )
            ]
            summary = f"Deployment state unknown: {type(exc).__name__}"

        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status="success",
                summary=summary,
                artifact_refs=artifact_refs,
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
                usage=UsageMetrics(),
            ),
            request=request,
            execution_mode="live",
            activity={
                "connector_tools": [
                    "resolve_deployment_target",
                    "start_deployment",
                    "get_rollout_status",
                    "verify_health",
                ],
                "outcome": data.get("deployment_outcome"),
            },
        )


class DeterministicExecutor:
    """Mode-level dispatcher for deterministic adapters."""

    executor_mode = "deterministic"
    adapter_ids = frozenset({"repository_inventory", "deployment_state_machine"})

    def __init__(self) -> None:
        self._adapters = {
            "repository_inventory": RepositoryInventoryExecutor(),
            "deployment_state_machine": DeploymentStateMachineExecutor(),
        }

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        adapter = request.descriptor.executor_adapter_id
        executor = self._adapters.get(adapter)
        if executor is None:
            return blocked_result(
                request,
                summary=f"unsupported deterministic adapter: {adapter}",
                activity={"reason": "unknown_adapter"},
            )
        return executor.execute(request)
