from __future__ import annotations

from product_factory.domain.capabilities import CAPABILITY_TOOL_CLASSES
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.registry import list_workflow_packs

DEPLOY_TOOLS = {
    "resolve_deployment_target",
    "start_deployment",
    "get_rollout_status",
    "verify_health",
    "rollback_deployment",
}


def test_deployment_tool_classes_are_exclusive_to_deployment_capability() -> None:
    for capability, classes in CAPABILITY_TOOL_CLASSES.items():
        if capability != "deployment_execution":
            assert not (classes & {"deployment_read", "deployment_write"}), capability


def test_all_read_only_packs_deny_or_cannot_grant_deploy_tools() -> None:
    for pack in list_workflow_packs():
        authority = handler_for(pack.id).authority_class()
        if authority == "external_write":
            assert pack.id == "deployment_execution"
            continue
        if authority not in {"read_only", "external_read"}:
            continue
        can_grant = pack.execution_policy.allowed_tool_classes & {
            "deployment_read",
            "deployment_write",
        }
        assert not can_grant or pack.execution_policy.denied_tool_names >= DEPLOY_TOOLS
