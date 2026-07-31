"""Pack handler plan templates and eligible next actions (PM0.A)."""

from __future__ import annotations

from product_factory.workflows.default_plans import (
    default_code_change_plan,
    default_quality_gate_plan,
    default_technical_plan,
)
from product_factory.workflows.handlers import eligible_next_actions_for_workflow, handler_for


def test_handler_plan_matches_default_plans() -> None:
    text = "add health endpoint"
    assert (
        handler_for("repository_change").plan_template(text).model_dump()
        == default_code_change_plan(text).model_dump()
    )
    assert (
        handler_for("technical_plan").plan_template(text).model_dump()
        == default_technical_plan(text).model_dump()
    )
    assert (
        handler_for("quality_gate").plan_template(text).model_dump()
        == default_quality_gate_plan(text).model_dump()
    )


def test_eligible_next_actions_projection() -> None:
    actions = eligible_next_actions_for_workflow("technical_plan")
    assert any(a["pack_id"] == "repository_change" for a in actions)
    actions = eligible_next_actions_for_workflow("architecture")
    assert any(a["pack_id"] == "repository_change" for a in actions)


def test_alias_handler_resolution() -> None:
    assert handler_for("code_change").pack_id == "repository_change"
    assert handler_for("architecture").pack_id == "technical_plan"
