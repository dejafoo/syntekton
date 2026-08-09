"""Registered pack-owned plan transforms.

Compatibility request metadata is interpreted here, outside shared lifecycle
code. Packs opt into transforms by stable ID.
"""

from __future__ import annotations

from collections.abc import Callable

from product_factory.domain.errors import ConfigurationError
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.workflows.base import WorkflowPack

PlanTransform = Callable[[PlannerOutput, RunRequest], PlannerOutput]


def _repository_change_compat(proposal: PlannerOutput, request: RunRequest) -> PlannerOutput:
    disable_review = request.metadata.get("disable_review") == "true"
    force_review = request.metadata.get("force_review") == "true"
    disable_analysis = request.metadata.get("disable_analysis") == "true"
    tasks = list(proposal.tasks)
    review_ids = {task.id for task in tasks if task.capability == "independent_review"}
    analysis_ids = {task.id for task in tasks if task.capability == "repository_analysis"}
    if disable_analysis and analysis_ids:
        tasks = [
            task.model_copy(
                update={
                    "dependencies": [dep for dep in task.dependencies if dep not in analysis_ids]
                }
            )
            for task in tasks
            if task.id not in analysis_ids
        ]
    if disable_review and review_ids:
        tasks = [
            task.model_copy(
                update={"dependencies": [dep for dep in task.dependencies if dep not in review_ids]}
            )
            for task in tasks
            if task.id not in review_ids
        ]
    elif force_review and not review_ids:
        implementation = next(
            (task for task in tasks if task.capability in {"implementation", "repair"}), None
        )
        composition_index = next(
            (index for index, task in enumerate(tasks) if task.capability == "composition"),
            len(tasks),
        )
        if implementation is not None:
            review = TaskSpec(
                id="POLICY-REVIEW",
                title="Independent review",
                capability="independent_review",
                objective="Review the proposed patch with evidence",
                dependencies=[implementation.id],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read", "git_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="POLICY-REVIEW-AC1",
                        description="Findings cite file or patch evidence",
                        verification="evidence_check",
                    )
                ],
            )
            tasks.insert(composition_index, review)
            tasks = [
                task.model_copy(
                    update={
                        "dependencies": [*task.dependencies, review.id]
                        if task.capability == "composition"
                        else task.dependencies
                    }
                )
                for task in tasks
            ]

    expected = [
        path.strip()
        for path in str(request.metadata.get("expected_files") or "").split(",")
        if path.strip()
    ]
    if expected:
        guidance = (
            "Required deliverable paths (create or modify exactly these paths):\n"
            + "\n".join(f"- {path}" for path in expected)
        )
        guided: list[TaskSpec] = []
        for task in tasks:
            if task.capability not in {"implementation", "repair"}:
                guided.append(task)
                continue
            objective = task.objective
            if "Required deliverable paths" not in objective:
                objective = f"{objective.rstrip()}\n\n{guidance}"
            guided.append(task.model_copy(update={"objective": objective}))
        tasks = guided
    return proposal.model_copy(update={"tasks": tasks})


_TRANSFORMS: dict[str, PlanTransform] = {
    "repository_change_compat": _repository_change_compat,
}


def apply_plan_transforms(
    proposal: PlannerOutput, *, request: RunRequest, pack: WorkflowPack | None
) -> PlannerOutput:
    transformed = proposal
    for transform_id in pack.plan_transforms if pack is not None else ():
        try:
            transform = _TRANSFORMS[transform_id]
        except KeyError as exc:
            raise ConfigurationError(f"Unknown plan transform {transform_id!r}") from exc
        transformed = transform(transformed, request)
    return transformed
