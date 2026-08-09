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
    grantable_connector_names_for_task,
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
    cap = pack.execution_policy.capability_policies["requirements"]
    granted, _, _ = compute_allowed_tool_names(
        task=task,
        request=request,
        tool_registry=default_tool_registry(),
        connector_tool_names=frozenset(),
        grantable_connector_tools=frozenset(),
        denied_tool_names=pack.execution_policy.denied_tool_names,
        pack_allowed_tool_classes=cap.allowed_tool_classes,
    )
    assert "create_file" not in granted
    assert "apply_patch" not in granted
    assert "web_search" not in granted
    assert "fetch_source" not in granted


def test_documentation_cannot_receive_write_classes_via_pack_union() -> None:
    """Per-capability grant must not inherit pack-wide repository_write."""
    pack = resolve_workflow_pack("repository_change")
    assert "repository_write" in pack.execution_policy.allowed_tool_classes
    doc = pack.execution_policy.capability_policies["documentation"]
    assert "repository_write" not in doc.allowed_tool_classes
    task = TaskSpec(
        id="doc-1",
        title="Docs",
        capability="documentation",
        objective="write docs",
        expected_output_schema="composition_result.v1",
        required_tool_classes=["artifact_write"],
        budget=TaskBudget(max_tool_calls=4, max_cost_usd="0.50"),
    )
    request = RunRequest(
        request_id="req-doc",
        request_text="docs",
        workflow_type="repository_change",
    )
    granted, _, classes = compute_allowed_tool_names(
        task=task,
        request=request,
        tool_registry=default_tool_registry(),
        connector_tool_names=frozenset(),
        grantable_connector_tools=frozenset(),
        denied_tool_names=pack.execution_policy.denied_tool_names,
        pack_allowed_tool_classes=doc.allowed_tool_classes,
    )
    assert "create_file" not in granted
    assert "apply_patch" not in granted
    assert "repository_write" not in classes
    assert "write_artifact" in granted


def test_prep_and_compiler_do_not_pass_pack_wide_tool_union() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "product_factory"
    prep = (root / "orchestration" / "task_preparation.py").read_text(encoding="utf-8")
    compiler = (root / "planning" / "compiler.py").read_text(encoding="utf-8")
    assert "pack_allowed_tool_classes=cap_policy.allowed_tool_classes" in prep
    assert "execution_policy.allowed_tool_classes)," not in prep
    assert "cap_policy.allowed_tool_classes" in compiler
    assert "execution_policy.allowed_tool_classes\n            ):" not in compiler


def test_implementation_grants_come_from_descriptor_intersection() -> None:
    pack = resolve_workflow_pack("repository_change")
    cap = pack.execution_policy.capability_policies["implementation"]
    task = TaskSpec(
        id="impl-1",
        title="Implement",
        capability="implementation",
        objective="ship",
        expected_output_schema="change_set.patch.v1",
        required_tool_classes=[
            "repository_read",
            "repository_write",
            "git_read",
            "validation_command",
        ],
        budget=TaskBudget(max_tool_calls=8, max_cost_usd="1.00"),
    )
    request = RunRequest(
        request_id="req-impl",
        request_text="change",
        workflow_type="repository_change",
    )
    granted, _, _ = compute_allowed_tool_names(
        task=task,
        request=request,
        tool_registry=default_tool_registry(),
        connector_tool_names=frozenset(),
        grantable_connector_tools=frozenset(),
        pack_allowed_tool_classes=cap.allowed_tool_classes,
    )
    assert "create_file" in granted
    assert "apply_patch" in granted
    assert "read_file" in granted
    assert "run_validation_command" in granted


def test_empty_task_tool_request_grants_no_tools() -> None:
    task = TaskSpec(
        id="empty-tools",
        title="Read only draft",
        capability="documentation",
        objective="draft",
        expected_output_schema="document.v1",
        required_tool_classes=[],
        budget=TaskBudget(max_tool_calls=1, max_cost_usd="0.10"),
    )
    granted, _, classes = compute_allowed_tool_names(
        task=task,
        request=RunRequest(
            request_id="empty-tools", request_text="draft", workflow_type="code_change"
        ),
        tool_registry=default_tool_registry(),
        connector_tool_names=frozenset(),
        grantable_connector_tools=frozenset(),
        pack_allowed_tool_classes=frozenset({"artifact_write"}),
    )
    assert granted == set()
    assert classes == []


def test_unrequested_connector_is_not_granted() -> None:
    task = TaskSpec(
        id="no-web",
        title="Research without retrieval",
        capability="domain_research",
        objective="reason from dependencies",
        expected_output_schema="research.v1",
        required_tool_classes=["artifact_write"],
        budget=TaskBudget(max_tool_calls=1, max_cost_usd="0.10"),
    )
    seen: set[str] = set()

    def grantable(classes: set[str]) -> set[str]:
        nonlocal seen
        seen = classes
        return {"web_search"} if "web_read" in classes else set()

    grant = grantable_connector_names_for_task(
        task=task,
        grantable_fn=grantable,
        allowed_connector_classes=frozenset({"web_read"}),
    )
    assert seen == set()
    assert grant == frozenset()


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
