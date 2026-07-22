"""Plan compiler unit tests."""

from __future__ import annotations

from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.planning.compiler import compile_plan


def _valid_plan() -> PlannerOutput:
    return PlannerOutput(
        objective="demo",
        tasks=[
            TaskSpec(
                id="T-001",
                title="analyze",
                capability="repository_analysis",
                objective="look",
                expected_output_schema="a.v1",
                required_tool_classes={"repository_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac1", description="d", verification="evidence_check")
                ],
            ),
            TaskSpec(
                id="T-002",
                title="impl",
                capability="implementation",
                objective="change",
                dependencies=["T-001"],
                expected_output_schema="i.v1",
                required_tool_classes={"repository_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac2", description="d", verification="test_suite")
                ],
            ),
            TaskSpec(
                id="T-003",
                title="compose",
                capability="composition",
                objective="compose",
                dependencies=["T-002"],
                expected_output_schema="c.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac3", description="d", verification="artifact_check")
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(logical_name="proposed.patch", composer_task_id="T-003")
        ],
    )


def test_valid_plan_compiles() -> None:
    result = compile_plan(_valid_plan())
    assert result.ok
    assert result.plan is not None
    assert result.plan.task_order == ["T-001", "T-002", "T-003"]


def test_cycle_rejected() -> None:
    plan = _valid_plan()
    plan.tasks[0].dependencies = ["T-002"]
    plan.tasks[1].dependencies = ["T-001"]
    result = compile_plan(plan)
    assert not result.ok
    assert any(e.code == "cyclic_dependency" for e in result.errors)


def test_unknown_capability_rejected() -> None:
    plan = _valid_plan()
    raw = plan.model_dump()
    raw["tasks"][0]["capability"] = "telepathy"
    # Bypass TaskSpec validation by constructing Compiler path via model_construct-ish:
    # Instead mutate after creating with a valid capability then force attribute.
    plan.tasks[0].capability = "telepathy"  # type: ignore[assignment]
    result = compile_plan(plan)
    assert not result.ok
    assert any(e.code == "unknown_capability" for e in result.errors)


def test_missing_validator_rejected() -> None:
    plan = PlannerOutput(
        objective="x",
        tasks=[
            TaskSpec(
                id="T-001",
                title="a",
                capability="documentation",
                objective="doc",
                expected_output_schema="d.v1",
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac", description="d", verification="llm_review")
                ],
            )
        ],
        final_artifacts=[FinalArtifactSpec(logical_name="x.md", composer_task_id="T-001")],
    )
    result = compile_plan(plan, require_baseline_validators=True)
    assert not result.ok
    assert any(e.code == "missing_baseline_validators" for e in result.errors)
