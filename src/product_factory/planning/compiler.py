"""Deterministic plan compiler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from product_factory.domain.capabilities import CAPABILITIES, CAPABILITY_TOOL_CLASSES
from product_factory.domain.plans import (
    CompiledPlan,
    CompilerError,
    CompileResult,
    PlannerOutput,
)
from product_factory.schemas.builtin import resolve_output_schema_id
from product_factory.schemas.registry import SchemaRegistry, default_schema_registry

if TYPE_CHECKING:
    from product_factory.skills.registry import SkillRegistry
    from product_factory.workflows.base import WorkflowPack


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


def _skill_known(registry: SkillRegistry, skill_id: str) -> bool:
    for skill in registry.skills:
        if skill.manifest.id == skill_id or skill.manifest.title == skill_id:
            return True
    return False


def compile_plan(
    proposal: PlannerOutput,
    *,
    max_tasks: int = 20,
    max_parallel_tasks: int = 3,
    require_baseline_validators: bool = True,
    workflow_pack: WorkflowPack | None = None,
    skill_registry: SkillRegistry | None = None,
    schema_registry: SchemaRegistry | None = None,
    enforce_output_schemas: bool | None = None,
    profile_digests: dict[str, str] | None = None,
) -> CompileResult:
    errors: list[CompilerError] = []
    notes_pending: list[str] = []
    task_ids = [t.id for t in proposal.tasks]
    schemas = schema_registry or default_schema_registry()
    # Pack-backed compiles always enforce registry membership; bare unit tests may skip.
    check_schemas = (
        enforce_output_schemas
        if enforce_output_schemas is not None
        else workflow_pack is not None
    )
    pack_roles = (
        {spec.role for spec in workflow_pack.artifacts} if workflow_pack is not None else set()
    )
    skill_policy = dict(workflow_pack.skill_policy) if workflow_pack is not None else {}
    allow_skills = set(skill_policy.get("allow") or skill_policy.get("allowlist") or [])
    deny_skills = set(skill_policy.get("deny") or skill_policy.get("denylist") or [])

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
        if workflow_pack is not None and task.capability not in workflow_pack.allowed_capabilities:
            errors.append(
                CompilerError(
                    code="capability_not_allowed",
                    message=(
                        f"Capability {task.capability} not allowed by pack "
                        f"{workflow_pack.id}"
                    ),
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
        elif check_schemas:
            resolved = resolve_output_schema_id(task.expected_output_schema)
            spec = schemas.get(resolved)
            if spec is None:
                errors.append(
                    CompilerError(
                        code="unknown_output_schema",
                        message=f"Unknown expected_output_schema {task.expected_output_schema!r}",
                        task_id=task.id,
                    )
                )
            elif spec.reserved:
                errors.append(
                    CompilerError(
                        code="reserved_output_schema",
                        message=f"Schema {resolved!r} is reserved for a later phase",
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

        for skill_id in task.required_skills or []:
            if allow_skills and skill_id not in allow_skills:
                errors.append(
                    CompilerError(
                        code="skill_not_allowed",
                        message=f"Skill {skill_id!r} not in pack skill allowlist",
                        task_id=task.id,
                    )
                )
            if skill_id in deny_skills:
                errors.append(
                    CompilerError(
                        code="skill_denied",
                        message=f"Skill {skill_id!r} denied by pack skill policy",
                        task_id=task.id,
                    )
                )
            if skill_registry is not None and not _skill_known(skill_registry, skill_id):
                errors.append(
                    CompilerError(
                        code="unknown_skill",
                        message=f"Unknown required skill {skill_id!r}",
                        task_id=task.id,
                    )
                )
            elif skill_registry is not None:
                for skill in skill_registry.skills:
                    if skill.manifest.id == skill_id or skill.manifest.title == skill_id:
                        caps = set(skill.manifest.capabilities or [])
                        if caps and task.capability not in caps:
                            errors.append(
                                CompilerError(
                                    code="skill_capability_mismatch",
                                    message=(
                                        f"Skill {skill_id!r} does not declare "
                                        f"capability {task.capability}"
                                    ),
                                    task_id=task.id,
                                )
                            )
                        break

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
            if workflow_pack is not None and fa.role and pack_roles and fa.role not in pack_roles:
                errors.append(
                    CompilerError(
                        code="unknown_artifact_role",
                        message=(
                            f"Final artifact role {fa.role!r} not in pack "
                            f"{workflow_pack.id} land map"
                        ),
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
        profile_digests=dict(sorted((profile_digests or {}).items())),
        compiler_notes=notes,
    )
    return CompileResult(ok=True, plan=plan, errors=[])
