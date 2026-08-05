from __future__ import annotations

from pathlib import Path

from product_factory.context.task_context import resolve_skill_budget, skill_bundle_chars
from product_factory.skills.registry import SkillRegistry


def test_change_control_skill_is_checklist_not_authority() -> None:
    root = Path(__file__).resolve().parents[2]
    skill = SkillRegistry.load(root / "skills").get("deployment.change-control")
    assert skill is not None
    assert skill.manifest.output_schema_id == "deployment_record.v1"
    assert skill.manifest.capabilities == ["deployment_execution"]
    assert "composition" not in skill.manifest.capabilities
    assert not skill.manifest.required_tools
    assert {"repository_write", "git_write"} <= set(skill.manifest.prohibited_tools)
    text = (root / "skills/deployment/change-control/SKILL.md").read_text()
    assert "not deployment authority" in text
    assert "Never deploy to production" not in text  # manifest carries the negative trigger
    assert "Reject unknown, disabled, and" in text


def test_change_control_skill_is_not_selected_for_generic_composition() -> None:
    """PM5.B regression: deploy checklist must not inflate generic composition budgets."""
    root = Path(__file__).resolve().parents[2]
    registry = SkillRegistry.load(root / "skills")
    generic = registry.match(capability="composition")
    assert all(s.manifest.id != "deployment.change-control" for s in generic)
    assert all(s.manifest.id != "release.readiness-review" for s in generic)
    # Explicitly required composition (deployment pack) still receives the skill.
    deploy_compose = registry.match(
        capability="composition",
        required_skills=["deployment.change-control"],
        skill_policy={"allow": ["deployment.change-control"]},
    )
    assert [s.manifest.id for s in deploy_compose] == ["deployment.change-control"]
    budget = resolve_skill_budget(generic)
    if budget is not None:
        assert skill_bundle_chars(generic) <= budget
