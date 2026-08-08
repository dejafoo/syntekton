"""Rule-based scheduler and model selection."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.plans import CompiledPlan
from product_factory.domain.tasks import TaskSpec
from product_factory.registry.capability_descriptors import model_role_for


def select_model(task: TaskSpec, *, originating_profile: str | None = None) -> str:
    if task.preferred_model_profile:
        return task.preferred_model_profile
    if task.capability == "repair":
        return originating_profile or "coding_worker"
    try:
        return model_role_for(task.capability)
    except ConfigurationError as exc:
        raise ValueError(f"Unsupported capability: {task.capability}") from exc


def resolve_task_model_profile(
    task: TaskSpec,
    *,
    metadata: dict[str, Any] | None = None,
    originating_profile: str | None = None,
) -> str:
    """Select the model profile, honoring ablation overrides for impl/repair."""
    override = str((metadata or {}).get("implementation_model_profile") or "").strip()
    if override and task.capability in {"implementation", "repair"}:
        return override
    return select_model(task, originating_profile=originating_profile)


def runnable_tasks(
    plan: CompiledPlan,
    task_status: dict[str, str],
    *,
    max_parallel: int,
) -> list[TaskSpec]:
    done = {tid for tid, st in task_status.items() if st in {"success", "skipped"}}
    running = {tid for tid, st in task_status.items() if st == "running"}
    available: list[TaskSpec] = []
    for tid in plan.task_order:
        status = task_status.get(tid, "pending")
        if status != "pending":
            continue
        task = plan.tasks[tid]
        if all(dep in done for dep in task.dependencies):
            available.append(task)
    slots = max(0, max_parallel - len(running))
    return available[:slots]
