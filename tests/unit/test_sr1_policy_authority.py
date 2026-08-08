"""SR1 — capability execution policy authority and least privilege."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from product_factory.domain.capabilities import CAPABILITY_TOOL_CLASSES
from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskBudget, TaskSpec
from product_factory.orchestration.effective_policy import (
    compute_allowed_tool_names,
    resolve_effective_task_policy,
)
from product_factory.persistence.database import Database
from product_factory.tools.registry import default_tool_registry
from product_factory.trust.approvals import verify_deployment_action_approval
from product_factory.workflows.base import (
    CapabilityExecutionPolicy,
    PackExecutionPolicy,
    execution_policy,
)
from product_factory.workflows.registry import resolve_workflow_pack
from product_factory.workflows.repository_change import (
    REPOSITORY_CHANGE_CAPABILITIES,
    REPOSITORY_CHANGE_PACK,
)


def test_repository_change_excludes_deployment_execution() -> None:
    pack = resolve_workflow_pack("repository_change")
    assert pack.allowed_capabilities == REPOSITORY_CHANGE_CAPABILITIES
    assert "deployment_execution" not in pack.allowed_capabilities
    assert REPOSITORY_CHANGE_PACK.allowed_capabilities == REPOSITORY_CHANGE_CAPABILITIES
    assert "deployment_execution" not in REPOSITORY_CHANGE_CAPABILITIES


def test_capability_policies_cover_every_allowed_capability() -> None:
    pack = resolve_workflow_pack("repository_change")
    policy = pack.execution_policy
    assert set(policy.capability_policies) == set(pack.allowed_capabilities)
    for capability in pack.allowed_capabilities:
        cap_policy = policy.capability_policies[capability]
        assert cap_policy.capability_id == capability
        assert cap_policy.allowed_tool_classes <= CAPABILITY_TOOL_CLASSES[capability]


def test_pack_rejects_widening_tool_classes_beyond_descriptor() -> None:
    capabilities = frozenset({"documentation"})
    base = execution_policy(
        capabilities=capabilities,
        validators=["document_sections"],
        output_roles=("architecture_document",),
    )
    widened = replace(
        base,
        capability_policies={
            "documentation": replace(
                base.capability_policies["documentation"],
                allowed_tool_classes=frozenset(
                    {"artifact_write", "deployment_write"}  # beyond documentation descriptor
                ),
            )
        },
    )
    with pytest.raises(ConfigurationError, match="widened_capability_grants"):
        widened.validate(pack_id="bogus", capabilities=capabilities)


def test_execution_policy_auto_builds_capability_policies() -> None:
    capabilities = frozenset({"implementation", "repair", "composition"})
    policy = execution_policy(
        capabilities=capabilities,
        validators=["patch_applies"],
        output_roles=("proposed_patch",),
        repair_eligible_capabilities=frozenset({"implementation", "repair"}),
        approval_required=True,
    )
    assert set(policy.capability_policies) == set(capabilities)
    assert policy.capability_policies["implementation"].repair_eligible is True
    assert policy.capability_policies["composition"].repair_eligible is False
    assert policy.capability_policies["implementation"].approval_required is True
    assert policy.capability_policies["implementation"].external_action_requires_approval is False
    deploy = execution_policy(
        capabilities=frozenset({"deployment_execution", "composition"}),
        validators=["deployment_record_contract"],
        output_roles=("deployment_record",),
        approval_required=True,
    )
    assert deploy.capability_policies["deployment_execution"].external_action_requires_approval
    assert not deploy.capability_policies["composition"].external_action_requires_approval
    payload = policy.as_payload()
    assert "capability_policies" in payload
    assert set(payload["capability_policies"]) == set(capabilities)


def test_compute_allowed_tool_names_has_no_workflow_type_strip_sets() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "product_factory"
        / "orchestration"
        / "effective_policy.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(node, ast.Assign) and isinstance(target, ast.Name)
    }
    assert "_READ_ONLY_STRIP_WORKFLOW_TYPES" not in names
    assert "_INTAKE_WORKFLOW_TYPES" not in names
    assert "_QUALITY_GATE_WORKFLOW_TYPES" not in names


def test_change_intake_denied_tools_still_strip_writes() -> None:
    pack = resolve_workflow_pack("change_intake")
    task = TaskSpec(
        id="req-1",
        title="Requirements",
        capability="requirements",
        objective="capture request",
        expected_output_schema="change_brief.v1",
        required_tool_classes=["repository_read", "artifact_write"],
        budget=TaskBudget(max_tool_calls=4, max_cost_usd="0.50"),
    )
    request = RunRequest(
        request_id="req-intake",
        request_text="intake",
        workflow_type="change_intake",
    )
    granted, _, _ = compute_allowed_tool_names(
        task=task,
        request=request,
        tool_registry=default_tool_registry(),
        connector_tool_names=frozenset(),
        grantable_connector_tools=frozenset(),
        denied_tool_names=pack.execution_policy.denied_tool_names,
        pack_allowed_tool_classes=pack.execution_policy.allowed_tool_classes,
    )
    assert "create_file" not in granted
    assert "apply_patch" not in granted
    assert "web_search" not in granted
    assert "fetch_source" not in granted


def test_verify_deployment_action_approval_is_capability_gated(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "db.sqlite")
    request = RunRequest(
        request_id="req-deploy-gate",
        request_text="deploy",
        workflow_type="deployment_execution",
        pack_input={
            "approval_id": "approval-should-not-matter",
            "release_plan_digest": "a" * 64,
            "artifact_digest": "b" * 64,
            "target_id": "simulated-local",
            "idempotency_key": "k1",
        },
    )
    assert (
        verify_deployment_action_approval(
            db,
            request,
            consumer_run_id="run-1",
            capability="composition",
        )
        is False
    )


def test_repository_change_pack_does_not_grant_deployment_authority() -> None:
    pack = REPOSITORY_CHANGE_PACK
    assert "deployment_execution" not in pack.allowed_capabilities
    assert "deployment_execution" not in pack.execution_policy.capability_policies
    assert "deployment_write" not in pack.execution_policy.allowed_tool_classes
    assert "deployment_read" not in pack.execution_policy.allowed_tool_classes
    # Negative: grafting deployment tools onto a non-deployment capability fails.
    base = pack.execution_policy
    impl = base.capability_policies["implementation"]
    widened = replace(
        base,
        capability_policies={
            **base.capability_policies,
            "implementation": replace(
                impl,
                allowed_tool_classes=impl.allowed_tool_classes | {"deployment_write"},
            ),
        },
    )
    with pytest.raises(ConfigurationError, match="widened_capability_grants"):
        widened.validate(pack_id="repository_change", capabilities=pack.allowed_capabilities)


def test_resolve_effective_task_policy_accepts_pack_flags() -> None:
    task = TaskSpec(
        id="impl-1",
        title="Implement",
        capability="implementation",
        objective="ship",
        expected_output_schema="change_set.patch.v1",
        required_tool_classes=["filesystem_read", "filesystem_write", "git", "validation"],
        budget=TaskBudget(max_tool_calls=8, max_cost_usd="1.00"),
    )
    request = RunRequest(
        request_id="req-sr1",
        request_text="change",
        workflow_type="repository_change",
    )
    policy = resolve_effective_task_policy(
        run_id="run-sr1",
        task=task,
        request=request,
        tool_registry=default_tool_registry(),
        model_profile="mock",
        agent_profile="implementation_worker",
        skill_ids=[],
        repair_eligible=True,
        approval_required=False,
    )
    assert policy.repair_eligible is True
    assert policy.approval_required is False


def test_pack_execution_policy_rejects_unknown_capability_policy() -> None:
    policy = PackExecutionPolicy(
        executor_modes={"documentation": "model_draft"},
        allowed_tool_classes=frozenset({"artifact_write"}),
        validators=(),
        output_roles=("report",),
        capability_policies={
            "documentation": CapabilityExecutionPolicy(
                capability_id="documentation",
                executor_mode="model_draft",
                allowed_tool_classes=frozenset({"artifact_write"}),
            ),
            "not_a_capability": CapabilityExecutionPolicy(
                capability_id="not_a_capability",
                executor_mode="model_draft",
                allowed_tool_classes=frozenset(),
            ),
        },
    )
    with pytest.raises(ConfigurationError, match="unknown_capability_policies"):
        policy.validate(pack_id="bogus", capabilities=frozenset({"documentation"}))
