"""Skill package digests and fail-closed budget (PM0.C)."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.context.task_context import build_task_context
from product_factory.domain.errors import BudgetExhaustedError
from product_factory.skills.registry import Skill, SkillManifest, SkillRegistry


def test_skill_registry_computes_digest() -> None:
    registry = SkillRegistry.load(Path("skills"))
    skill = registry.get("repository-inspection")
    assert skill is not None
    assert len(skill.package_digest) == 64


def test_over_budget_skill_bundle_fails() -> None:
    manifest = SkillManifest.model_validate(
        {
            "id": "huge.skill",
            "version": "1.0.0",
            "title": "Huge",
            "capabilities": ["implementation"],
            "max_prompt_chars": 10,
        }
    )
    skill = Skill(manifest=manifest, content="x" * 500, path=Path("."), package_digest="a" * 64)
    with pytest.raises(BudgetExhaustedError):
        build_task_context(
            task_id="T-001",
            skills=[skill],
            tool_names=["read_file"],
            expected_output_schema="change_set.patch.v1",
        )
