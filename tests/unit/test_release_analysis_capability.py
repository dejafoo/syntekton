from __future__ import annotations

from product_factory.domain.capabilities import CAPABILITY_TOOL_CLASSES
from product_factory.domain.tasks import TaskSpec
from product_factory.scheduling.scheduler import select_model


def test_release_and_operations_capabilities_are_read_only_and_fast_worker_routed() -> None:
    release = CAPABILITY_TOOL_CLASSES["release_analysis"]
    operations = CAPABILITY_TOOL_CLASSES["operations_analysis"]
    assert {"ci_read", "ops_read"} <= release
    assert "ops_read" in operations
    for classes in (release, operations):
        assert not (classes & {"deployment_read", "deployment_write", "repository_write"})
    for capability in ("release_analysis", "operations_analysis"):
        task = TaskSpec(
            id="T",
            title="review",
            capability=capability,
            objective="review",
            expected_output_schema="review_findings.v1",
            acceptance_criteria=[],
        )
        assert select_model(task) == "fast_worker"
