"""N4 ablation knobs: planner mode and implementation worker profile."""

from __future__ import annotations

from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.scheduling.scheduler import (
    resolve_task_model_profile,
    select_model,
)


def _impl_task() -> TaskSpec:
    return TaskSpec(
        id="T-1",
        title="Implement",
        capability="implementation",
        objective="x",
        expected_output_schema="implementation_result.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="ac1", description="ok", verification="test_suite")
        ],
    )


def test_select_model_defaults_implementation_to_coding_worker() -> None:
    assert select_model(_impl_task()) == "coding_worker"


def test_resolve_task_model_profile_honors_implementation_override() -> None:
    task = _impl_task()
    assert (
        resolve_task_model_profile(
            task, metadata={"implementation_model_profile": "local_target_reviewer"}
        )
        == "local_target_reviewer"
    )
    assert resolve_task_model_profile(task, metadata={}) == "coding_worker"
