"""Compiler pack/schema/skill checks (PM0.B)."""

from __future__ import annotations

from pathlib import Path

from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.planning.compiler import compile_plan
from product_factory.skills.registry import SkillRegistry
from product_factory.workflows.default_plans import default_quality_gate_plan
from product_factory.workflows.registry import resolve_workflow_pack


def test_quality_gate_plan_compiles_with_pack() -> None:
    pack = resolve_workflow_pack("quality_gate")
    skills = SkillRegistry.load(Path("skills"))
    result = compile_plan(
        default_quality_gate_plan("review quality"),
        workflow_pack=pack,
        skill_registry=skills,
    )
    assert result.ok, result.errors


def test_disallowed_capability_rejected() -> None:
    pack = resolve_workflow_pack("quality_gate")
    plan = PlannerOutput(
        objective="x",
        tasks=[
            TaskSpec(
                id="T-001",
                title="impl",
                capability="implementation",
                objective="write",
                expected_output_schema="change_set.patch.v1",
                required_tool_classes={"repository_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac", description="d", verification="test_suite")
                ],
            ),
            TaskSpec(
                id="T-002",
                title="compose",
                capability="composition",
                objective="c",
                dependencies=["T-001"],
                expected_output_schema="test_plan.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac2", description="d", verification="static_rule")
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="TEST_PLAN.md",
                composer_task_id="T-002",
                role="test_plan",
            )
        ],
    )
    result = compile_plan(plan, workflow_pack=pack)
    assert not result.ok
    assert any(e.code == "capability_not_allowed" for e in result.errors)


def test_reserved_schema_rejected() -> None:
    pack = resolve_workflow_pack("technical_plan")
    plan = PlannerOutput(
        objective="x",
        tasks=[
            TaskSpec(
                id="T-001",
                title="brief",
                capability="architecture",
                objective="x",
                # PM1 un-reserves feasibility_dossier.v1; change_brief.v1 stays
                # reserved until PM2 ships change_intake.
                expected_output_schema="change_brief.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(id="ac", description="d", verification="artifact_check")
                ],
            )
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="ARCHITECTURE.md",
                composer_task_id="T-001",
                role="architecture_document",
            )
        ],
    )
    result = compile_plan(plan, workflow_pack=pack, require_baseline_validators=False)
    assert not result.ok
    assert any(e.code == "reserved_output_schema" for e in result.errors)
