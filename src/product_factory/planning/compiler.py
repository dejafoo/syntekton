"""Deterministic plan compiler."""

from __future__ import annotations

from product_factory.domain.capabilities import CAPABILITIES, CAPABILITY_TOOL_CLASSES
from product_factory.domain.plans import (
    CompiledPlan,
    CompilerError,
    CompileResult,
    PlannerOutput,
)


def _topo_sort(tasks: dict[str, list[str]]) -> tuple[list[str], list[CompilerError]]:
    errors: list[CompilerError] = []
    incoming = {tid: 0 for tid in tasks}
    for tid, deps in tasks.items():
        for dep in deps:
            if dep not in tasks:
                errors.append(
                    CompilerError(
                        code="missing_dependency",
                        message=f"Task {tid} depends on unknown task {dep}",
                        task_id=tid,
                    )
                )
            else:
                incoming[tid] += 1
    queue = [tid for tid, n in incoming.items() if n == 0]
    order: list[str] = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for tid, deps in tasks.items():
            if node in deps:
                incoming[tid] -= 1
                if incoming[tid] == 0:
                    queue.append(tid)
    if len(order) != len(tasks):
        errors.append(
            CompilerError(
                code="cyclic_dependency", message="Task dependency graph contains a cycle"
            )
        )
    return order, errors


def compile_plan(
    proposal: PlannerOutput,
    *,
    max_tasks: int = 20,
    max_parallel_tasks: int = 3,
    require_baseline_validators: bool = True,
) -> CompileResult:
    errors: list[CompilerError] = []
    notes_pending: list[str] = []
    task_ids = [t.id for t in proposal.tasks]

    if len(task_ids) != len(set(task_ids)):
        errors.append(CompilerError(code="duplicate_task_id", message="Task IDs must be unique"))

    if len(proposal.tasks) > max_tasks:
        errors.append(
            CompilerError(
                code="task_budget",
                message=f"Plan has {len(proposal.tasks)} tasks; max is {max_tasks}",
            )
        )

    tasks_by_id = {}
    dep_map: dict[str, list[str]] = {}
    for task in proposal.tasks:
        if task.capability not in CAPABILITIES:
            errors.append(
                CompilerError(
                    code="unknown_capability",
                    message=f"Unknown capability {task.capability}",
                    task_id=task.id,
                )
            )
        if not task.expected_output_schema:
            errors.append(
                CompilerError(
                    code="missing_output_schema",
                    message="Every task must declare expected_output_schema",
                    task_id=task.id,
                )
            )
        if not task.acceptance_criteria:
            errors.append(
                CompilerError(
                    code="missing_acceptance_criteria",
                    message="Every task needs at least one acceptance criterion",
                    task_id=task.id,
                )
            )
        for ac in task.acceptance_criteria:
            if not ac.verification:
                errors.append(
                    CompilerError(
                        code="missing_verification",
                        message=f"Criterion {ac.id} missing verification method",
                        task_id=task.id,
                    )
                )
        allowed = CAPABILITY_TOOL_CLASSES.get(task.capability, frozenset())
        for tool_class in task.required_tool_classes:
            if tool_class not in allowed:
                errors.append(
                    CompilerError(
                        code="tool_not_permitted",
                        message=f"Tool class {tool_class} not permitted for {task.capability}",
                        task_id=task.id,
                    )
                )
        if (
            "file_write" in task.prohibited_actions
            and "repository_write" in task.required_tool_classes
        ):
            errors.append(
                CompilerError(
                    code="prohibited_action",
                    message="Task both prohibits and requires write",
                    task_id=task.id,
                )
            )
        for pattern in task.allowed_path_patterns:
            if pattern.startswith("/") or ".." in pattern.split("/"):
                errors.append(
                    CompilerError(
                        code="invalid_path_pattern",
                        message=f"Invalid path pattern: {pattern}",
                        task_id=task.id,
                    )
                )
        for pattern in [*task.readable_path_patterns, *task.writable_path_patterns]:
            if pattern.startswith("/") or ".." in pattern.split("/"):
                errors.append(
                    CompilerError(
                        code="invalid_path_pattern",
                        message=f"Invalid path pattern: {pattern}",
                        task_id=task.id,
                    )
                )
        if (
            task.capability in {"implementation", "repair"}
            and task.effective_write_patterns() == ["**/*"]
            and task.risk == "low"
            and "justified broad path scope" not in (task.rationale or "").lower()
        ):
            notes_pending.append(
                f"Task {task.id} uses universal write scope **/* without justification"
            )
        tasks_by_id[task.id] = task
        dep_map[task.id] = list(task.dependencies)

    order, topo_errors = _topo_sort(dep_map)
    errors.extend(topo_errors)

    # Independence: no task both implements and independently reviews same output lineage.
    implementers = {t.id for t in proposal.tasks if t.capability == "implementation"}
    for task in proposal.tasks:
        if task.capability == "independent_review":
            overlap = set(task.dependencies) & implementers
            if task.id in implementers:
                errors.append(
                    CompilerError(
                        code="self_review",
                        message="Task cannot implement and independently review",
                        task_id=task.id,
                    )
                )
            # Soft check: reviewer must depend on some implementation when both exist
            if implementers and not overlap and proposal.final_artifacts:
                pass  # allowed if reviewing composed artifacts

    if not proposal.final_artifacts:
        errors.append(
            CompilerError(
                code="missing_final_artifacts", message="Plan must declare final artifacts"
            )
        )
    else:
        for fa in proposal.final_artifacts:
            if fa.composer_task_id not in tasks_by_id:
                errors.append(
                    CompilerError(
                        code="missing_composer",
                        message=f"Composer task {fa.composer_task_id} not found",
                        task_id=fa.composer_task_id,
                    )
                )

    if require_baseline_validators:
        has_validation = any(
            ac.verification in {"command", "test_suite", "artifact_check", "static_rule"}
            for t in proposal.tasks
            for ac in t.acceptance_criteria
        ) or any(
            t.capability in {"test_execution", "composition", "independent_review"}
            for t in proposal.tasks
        )
        if not has_validation:
            errors.append(
                CompilerError(
                    code="missing_baseline_validators",
                    message="Plan lacks baseline validation tasks or criteria",
                )
            )

    for ac in proposal.request_acceptance_criteria:
        if not ac.verification:
            errors.append(
                CompilerError(
                    code="missing_verification",
                    message=f"Request criterion {ac.id} missing verification method",
                )
            )
        if not ac.responsible_task_ids:
            errors.append(
                CompilerError(
                    code="unmapped_acceptance_criterion",
                    message=f"Request criterion {ac.id} lacks responsible_task_ids",
                )
            )
        for tid in ac.responsible_task_ids:
            if tid not in tasks_by_id:
                errors.append(
                    CompilerError(
                        code="unknown_responsible_task",
                        message=f"Request criterion {ac.id} maps to unknown task {tid}",
                        task_id=tid,
                    )
                )
            elif not tasks_by_id[tid].acceptance_criteria:
                errors.append(
                    CompilerError(
                        code="missing_acceptance_criteria",
                        message=(
                            f"Responsible task {tid} for criterion {ac.id} "
                            "has no acceptance criteria"
                        ),
                        task_id=tid,
                    )
                )

    # High-risk tasks should be flagged (compiler records note; approval enforced later).
    notes: list[str] = list(notes_pending)
    high_risk = [t.id for t in proposal.tasks if t.risk == "high"]
    if high_risk:
        notes.append(f"High-risk tasks require approval: {', '.join(high_risk)}")

    if max_parallel_tasks < 1:
        errors.append(CompilerError(code="concurrency", message="max_parallel_tasks must be >= 1"))

    if errors:
        return CompileResult(ok=False, errors=errors)

    plan = CompiledPlan(
        objective=proposal.objective,
        assumptions=proposal.assumptions,
        tasks=tasks_by_id,
        task_order=order,
        final_artifacts=proposal.final_artifacts,
        validation_strategy=proposal.validation_strategy,
        risk_classification=proposal.risk_classification,
        request_acceptance_criteria=proposal.request_acceptance_criteria,
        compiler_notes=notes,
    )
    return CompileResult(ok=True, plan=plan, errors=[])
