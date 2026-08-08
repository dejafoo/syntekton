"""RF4 generic pack policy, dispatch, and architecture contracts."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

from product_factory.domain.errors import ConfigurationError
from product_factory.planning.compiler import compile_plan
from product_factory.planning.planner import build_planner_messages
from product_factory.workflows.artifacts import ArtifactLandSpec
from product_factory.workflows.base import (
    PackExecutionPolicy,
    WorkflowPack,
    execution_policy,
)
from product_factory.workflows.handlers import (
    handler_for,
    register_pack_handler,
)
from product_factory.workflows.handlers.base import AuthorityClass, ComposeContext
from product_factory.workflows.registry import (
    list_workflow_packs,
    register_workflow_pack,
    resolve_workflow_pack,
)


def test_every_canonical_pack_compiles_through_registered_dispatch() -> None:
    # Ignore dynamic fixture packs registered by other tests in-process.
    builtin_ids = {
        "change_intake",
        "deployment_execution",
        "feasibility_discovery",
        "incident_triage",
        "quality_gate",
        "release_readiness",
        "repository_change",
        "repository_investigation",
        "service_health_review",
        "technical_plan",
        "technical_spike",
    }
    for pack in list_workflow_packs():
        if pack.id not in builtin_ids:
            continue
        handler = handler_for(pack.id)
        proposal = handler.plan_template(f"exercise {pack.id}")
        result = compile_plan(proposal, workflow_pack=pack)
        assert result.ok, (pack.id, result.errors)
        for task in proposal.tasks:
            assert pack.execution_policy.executor_mode_for(task.capability)


def test_pm5_canonical_packs_remain_on_registered_dispatch() -> None:
    """PM5.A/B/C/D: workflow packs stay on RF4 dispatch; domain packs do not."""
    expected = {
        "release_readiness",
        "deployment_execution",
        "incident_triage",
        "service_health_review",
    }
    registered = {pack.id for pack in list_workflow_packs()}
    assert expected <= registered
    for pack_id in expected:
        pack = resolve_workflow_pack(pack_id)
        handler = handler_for(pack_id)
        proposal = handler.plan_template(f"exercise {pack_id}")
        result = compile_plan(proposal, workflow_pack=pack)
        assert result.ok, (pack_id, result.errors)
    # Domain reference packs are evidence data, not workflow packs.
    assert "fhir-r4-public" not in registered


def test_live_planner_capabilities_are_pack_scoped() -> None:
    pack = resolve_workflow_pack("technical_spike")
    messages = build_planner_messages(
        request_text="analyze contract",
        workflow_type=pack.id,
        repository_summary=None,
        budget={},
        allowed_capabilities=pack.allowed_capabilities,
    )
    payload = json.loads(messages[-1].content)
    assert payload["capabilities"] == ["interface_analysis"]


@pytest.mark.parametrize(
    "policy",
    [
        PackExecutionPolicy(
            executor_modes={"documentation": "unknown"},  # type: ignore[dict-item]
            allowed_tool_classes=frozenset({"artifact_write"}),
            validators=(),
            output_roles=("report",),
        ),
        PackExecutionPolicy(
            executor_modes={"made_up": "model_draft"},
            allowed_tool_classes=frozenset({"artifact_write"}),
            validators=(),
            output_roles=("report",),
        ),
        PackExecutionPolicy(
            executor_modes={"documentation": "model_draft"},
            allowed_tool_classes=frozenset({"unknown_tool_class"}),
            validators=(),
            output_roles=("report",),
        ),
        PackExecutionPolicy(
            executor_modes={"documentation": "model_draft"},
            allowed_tool_classes=frozenset({"artifact_write"}),
            validators=("unknown_validator",),
            output_roles=("report",),
        ),
        PackExecutionPolicy(
            executor_modes={"documentation": "model_draft"},
            allowed_tool_classes=frozenset({"artifact_write"}),
            validators=(),
            output_roles=("report",),
            required_output_roles=frozenset({"missing"}),
        ),
    ],
)
def test_invalid_pack_execution_policy_fails_closed(
    policy: PackExecutionPolicy,
) -> None:
    with pytest.raises(ConfigurationError):
        policy.validate(
            pack_id="invalid",
            capabilities=frozenset({"documentation"}),
        )


def test_pack_role_mismatch_fails_at_registry_boundary() -> None:
    base = resolve_workflow_pack("repository_investigation")
    invalid = replace(
        base,
        id="rf4-invalid-role-pack",
        execution_policy=replace(
            base.execution_policy,
            output_roles=("not_the_artifact_role",),
        ),
    )
    with pytest.raises(ConfigurationError):
        register_workflow_pack(invalid)


class _ReadOnlyExampleHandler:
    pack_id = "rf4-read-only-example"

    def plan_template(self, request_text: str):
        return handler_for("repository_investigation").plan_template(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        return f"# Summary\n\n{ctx.request.request_text}\n"

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ("Summary",)

    def validator_id(self, role: str) -> str:
        return "document_sections"

    def authority_class(self) -> AuthorityClass:
        return "read_only"

    def eligible_next_actions(self):
        return []

    def findings_are_deliverable(self) -> bool:
        return False


def test_small_read_only_pack_registers_without_coordinator_branch() -> None:
    role = "example_report"
    capabilities = frozenset(
        {"repository_analysis", "independent_review", "documentation", "composition"}
    )
    pack = WorkflowPack(
        id="rf4-read-only-example",
        version="1.0.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        allowed_capabilities=capabilities,
        default_planner_mode="fixed",
        validation_policy={},
        skill_policy={},
        routing_defaults={},
        execution_policy=execution_policy(
            capabilities=capabilities,
            validators=["document_sections"],
            output_roles=(role,),
            required_output_roles=frozenset({role}),
        ),
        artifacts=(
            ArtifactLandSpec(
                role=role,
                default_logical_name="EXAMPLE.md",
                default_dest_path="docs/EXAMPLE.md",
            ),
        ),
    )
    register_workflow_pack(pack)
    register_pack_handler(_ReadOnlyExampleHandler())
    assert resolve_workflow_pack(pack.id) is pack
    assert handler_for(pack.id).pack_id == pack.id


def test_coordinator_is_lifecycle_facade_without_workflow_branches() -> None:
    """SD2/G2: RunCoordinator must stay a thin façade (no named workflow branches)."""
    coordinator = (
        Path(__file__).parents[2] / "src" / "product_factory" / "orchestration" / "coordinator.py"
    )
    source = coordinator.read_text(encoding="utf-8")
    tree = ast.parse(source)
    declared = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name) and target.id.endswith("_WORKFLOW_TYPES")
    }
    assert declared == set()
    assert "RunLifecycleEngine" in source
    assert "_compose_architecture" not in source
    assert "class RunCoordinator" in source
    assert "return self._engine._execute_task(*args, **kwargs)" in source


def test_lifecycle_engine_forbids_new_named_workflow_type_constants() -> None:
    """Architecture guard: no new *_WORKFLOW_TYPES frozensets in shared lifecycle code."""
    root = Path(__file__).parents[2] / "src" / "product_factory"
    offenders: list[str] = []
    for path in [
        root / "orchestration" / "lifecycle" / "engine.py",
        root / "orchestration" / "coordinator.py",
        root / "scheduling" / "scheduler.py",
        root / "orchestration" / "finalization" / "run_finalizer.py",
        root / "orchestration" / "validation_repair" / "service.py",
    ]:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.endswith("_WORKFLOW_TYPES"):
                    offenders.append(f"{path.name}:{target.id}")
    assert offenders == []
