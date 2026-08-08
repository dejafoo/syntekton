"""Task executor protocol and request/result contracts (SD1.B)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskResult, TaskSpec
from product_factory.orchestration.effective_policy import EffectiveTaskPolicy
from product_factory.registry.capability_descriptors import CapabilityDescriptor


@dataclass(slots=True)
class TaskExecutionRequest:
    """Everything a registered executor needs to perform declared work."""

    run_id: str
    run_dir: Path
    request: RunRequest
    task: TaskSpec
    effective_policy: EffectiveTaskPolicy
    descriptor: CapabilityDescriptor
    agent_profile: str
    model_profile: str
    broker: Any
    artifacts: Any
    gateway: Any
    raw_gateway: Any
    tool_registry: Any
    allow_deterministic_workers: bool
    ctx_messages: list[dict[str, str]]
    package_hash: str
    granted_tool_names: set[str]
    registered_command_ids: list[str]
    dependency_outputs: list[dict[str, Any]] = field(default_factory=list)
    repository_excerpts: list[dict[str, str]] = field(default_factory=list)
    base_commit: str = ""
    land_map: Any = None
    composer_role: str | None = None
    validation_evidence_refs: list[str] = field(default_factory=list)
    validator_results: list[dict[str, Any]] = field(default_factory=list)
    # Typed composition boundary (SD2); services bag only for non-compose helpers.
    composition: Any | None = None
    services: dict[str, Any] = field(default_factory=dict)


class TaskExecutor(Protocol):
    """Registered executor for one executor_mode (+ optional adapter)."""

    executor_mode: str
    adapter_ids: frozenset[str]

    def execute(self, request: TaskExecutionRequest) -> TaskResult: ...


def attach_receipt(
    result: TaskResult,
    *,
    request: TaskExecutionRequest,
    execution_mode: str,
    activity: dict[str, Any] | None = None,
) -> TaskResult:
    """Stamp executor identity and live/mock marker onto a task result."""

    result.executor_mode = request.effective_policy.executor_mode
    result.executor_adapter_id = request.descriptor.executor_adapter_id
    result.agent_profile_id = request.agent_profile
    result.parser_id = request.descriptor.parser_id
    result.execution_mode = execution_mode  # type: ignore[assignment]
    receipt = {
        "executor_mode": result.executor_mode,
        "executor_adapter_id": result.executor_adapter_id,
        "agent_profile_id": result.agent_profile_id,
        "parser_id": result.parser_id,
        "model_profile": request.model_profile,
        "execution_mode": execution_mode,
        "capability": request.task.capability,
        "result_schema_id": request.descriptor.result_schema_id,
    }
    if activity:
        receipt["activity"] = activity  # type: ignore[assignment]
    result.activity_receipt = receipt
    return result


def blocked_result(
    request: TaskExecutionRequest,
    *,
    summary: str,
    execution_mode: str = "live",
    activity: dict[str, Any] | None = None,
) -> TaskResult:
    return attach_receipt(
        TaskResult(
            task_id=request.task.id,
            status="blocked",
            summary=summary,
            model_profile=request.model_profile,
            resolved_model_id=request.model_profile,
            prompt_package_hash=request.package_hash,
        ),
        request=request,
        execution_mode=execution_mode,
        activity=activity,
    )


def unsupported_result(
    request: TaskExecutionRequest,
    *,
    summary: str,
    execution_mode: str = "live",
    activity: dict[str, Any] | None = None,
) -> TaskResult:
    return attach_receipt(
        TaskResult(
            task_id=request.task.id,
            status="unsupported",
            summary=summary,
            model_profile=request.model_profile,
            resolved_model_id=request.model_profile,
            prompt_package_hash=request.package_hash,
        ),
        request=request,
        execution_mode=execution_mode,
        activity=activity,
    )
