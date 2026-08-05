from __future__ import annotations

from product_factory.planning.compiler import compile_plan
from product_factory.workflows.handlers import eligible_next_actions_for_workflow, handler_for
from product_factory.workflows.registry import resolve_workflow_pack


def test_deployment_pack_is_external_write_and_approval_gated() -> None:
    pack = resolve_workflow_pack("deployment_execution")
    assert pack.execution_policy.approval_required is True
    assert handler_for(pack.id).authority_class() == "external_write"
    assert pack.allowed_capabilities == {"deployment_execution", "composition"}
    assert pack.execution_policy.allowed_tool_classes == {
        "deployment_read",
        "deployment_write",
        "artifact_write",
    }


def test_deployment_plan_compiles_with_tools_only_on_execution_task() -> None:
    pack = resolve_workflow_pack("deployment_execution")
    proposal = handler_for(pack.id).plan_template("Deploy the approved candidate to staging")
    result = compile_plan(proposal, workflow_pack=pack)
    assert result.ok, result.errors
    execution, composition = proposal.tasks
    assert execution.required_tool_classes == {"deployment_read", "deployment_write"}
    assert composition.required_tool_classes == {"artifact_write"}
    assert "production_deployment" in execution.prohibited_actions


def test_only_ready_release_plan_offers_deployment_next_action() -> None:
    for outcome in ("blocked", "needs_decision", None):
        actions = eligible_next_actions_for_workflow("release_readiness", outcome=outcome)
        assert all(item["pack_id"] != "deployment_execution" for item in actions)
    ready = eligible_next_actions_for_workflow("release_readiness", outcome="ready")
    assert any(item["pack_id"] == "deployment_execution" for item in ready)
