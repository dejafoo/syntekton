"""Context packing policy resolution."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import ContextPackingConfig, load_config
from product_factory.context.assembler import assemble_context, resolve_context_limits
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec


def test_policies_yaml_loads_context_section() -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    ctx = config.policies.context
    assert ctx.max_excerpt_chars == 20_000
    assert ctx.max_excerpt_files == 12
    assert ctx.max_manifest_chars == 40_000
    assert ctx.clamp_to_model_window is True
    assert ctx.chars_per_token == 4


def test_resolve_respects_policy_ceiling_over_large_task_budget() -> None:
    limits = resolve_context_limits(
        ContextPackingConfig(max_excerpt_chars=20_000, min_excerpt_chars=4_000),
        task_max_input_tokens=200_000,
    )
    assert limits.max_excerpt_chars == 20_000


def test_resolve_clamps_to_model_window_when_enabled() -> None:
    # 32k soft limit, 45% reserved → 17_600 usable tokens → 70_400 chars,
    # but policy max_excerpt_chars still wins when smaller.
    limits = resolve_context_limits(
        ContextPackingConfig(
            max_excerpt_chars=100_000,
            min_excerpt_chars=4_000,
            clamp_to_model_window=True,
            chars_per_token=4,
            model_window_reserve_ratio=0.45,
        ),
        task_max_input_tokens=200_000,
        model_context_soft_limit=32_000,
    )
    assert limits.max_excerpt_chars == int(32_000 * 0.55) * 4


def test_resolve_skips_model_window_when_disabled() -> None:
    limits = resolve_context_limits(
        ContextPackingConfig(
            max_excerpt_chars=20_000,
            clamp_to_model_window=False,
        ),
        task_max_input_tokens=50_000,
        model_context_soft_limit=8_000,
    )
    assert limits.max_excerpt_chars == 20_000


def test_assemble_context_uses_resolved_manifest_cap() -> None:
    task = TaskSpec(
        id="T-budget",
        title="bounded",
        capability="implementation",
        objective="change",
        expected_output_schema="implementation.v1",
        acceptance_criteria=[
            AcceptanceCriterion(id="a", description="d", verification="test_suite")
        ],
        budget=TaskBudget(
            max_input_tokens=3_000,
            max_output_tokens=1_000,
            max_tool_calls=5,
            max_repair_attempts=1,
            max_wall_clock_seconds=60,
        ),
    )
    packing = ContextPackingConfig(
        max_manifest_chars=8_000,
        min_manifest_chars=2_000,
        max_manifest_excerpts=5,
        clamp_to_model_window=False,
    )
    context = assemble_context(
        task=task,
        model_profile="coding_worker",
        agent_profile="implementation_worker",
        skills=[],
        tool_definitions=[{"name": "read_file"}],
        repository_excerpts=[{"path": "large.py", "content": "x" * 100_000}],
        package_id="bounded",
        packing=packing,
    )
    assert any("truncated" in item for item in context.manifest.omitted_context)
    assert context.manifest.estimated_tokens < 10_000
