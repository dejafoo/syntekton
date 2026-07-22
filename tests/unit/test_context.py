"""Context assembler and skill matching tests."""

from __future__ import annotations

from pathlib import Path

from product_factory.context.assembler import assemble_context
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.skills.registry import SkillRegistry


def test_skill_load_and_match() -> None:
    root = Path(__file__).resolve().parents[2]
    registry = SkillRegistry.load(root / "skills")
    assert registry.skills
    matched = registry.match(capability="independent_review")
    assert any(s.manifest.id == "quality.patch-review" for s in matched)


def test_assemble_context_reproducible() -> None:
    task = TaskSpec(
        id="T-1",
        title="t",
        capability="implementation",
        objective="obj",
        expected_output_schema="x.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="a", description="d", verification="test_suite")
        ],
    )
    a = assemble_context(
        task=task,
        model_profile="coding_worker",
        agent_profile="implementation_worker",
        skills=[],
        tool_definitions=[{"name": "read_file"}],
        package_id="p1",
    )
    b = assemble_context(
        task=task,
        model_profile="coding_worker",
        agent_profile="implementation_worker",
        skills=[],
        tool_definitions=[{"name": "read_file"}],
        package_id="p1",
    )
    assert a.package_hash == b.package_hash
    assert a.manifest.estimated_tokens > 0


def test_context_manifest_is_bounded_by_task_budget() -> None:
    task = TaskSpec(
        id="T-budget",
        title="bounded",
        capability="implementation",
        objective="change",
        expected_output_schema="implementation.v1",
        budget=TaskBudget(
            max_input_tokens=3_000,
            max_output_tokens=1_000,
            max_tool_calls=5,
            max_repair_attempts=1,
            max_wall_clock_seconds=60,
        ),
    )
    context = assemble_context(
        task=task,
        model_profile="coding_worker",
        agent_profile="implementation_worker",
        skills=[],
        tool_definitions=[{"name": "read_file"}],
        repository_excerpts=[{"path": "large.py", "content": "x" * 100_000}],
        package_id="bounded",
    )
    assert context.manifest.estimated_tokens < 3_000
    assert any("truncated" in item for item in context.manifest.omitted_context)
