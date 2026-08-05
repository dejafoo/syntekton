from __future__ import annotations

from product_factory.planning.compiler import compile_plan
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.registry import resolve_workflow_pack


def test_release_readiness_pack_is_registered_external_read_only() -> None:
    pack = resolve_workflow_pack("release_readiness")
    handler = handler_for(pack.id)
    assert handler.authority_class() == "external_read"
    assert pack.allowed_capabilities == {"release_analysis", "operations_analysis", "composition"}
    assert "release_plan_contract" in pack.execution_policy.validators
    assert "deployment_execution" not in pack.allowed_capabilities
    assert "start_deployment" in pack.execution_policy.denied_tool_names


def test_release_readiness_default_plan_compiles_without_deploy_tools() -> None:
    pack = resolve_workflow_pack("release_readiness")
    proposal = handler_for(pack.id).plan_template("Assess release candidate")
    result = compile_plan(proposal, workflow_pack=pack)
    assert result.ok, result.errors
    for task in proposal.tasks:
        assert task.capability != "deployment_execution"
        assert not (task.required_tool_classes & {"deployment_read", "deployment_write"})


def test_release_readiness_never_offers_deployment_next_action() -> None:
    actions = handler_for("release_readiness").eligible_next_actions()
    assert all(action.pack_id != "deployment_execution" for action in actions)
    quality_actions = handler_for("quality_gate").eligible_next_actions()
    assert any(action.pack_id == "release_readiness" for action in quality_actions)
