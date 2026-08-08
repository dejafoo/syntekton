"""SD2 kernel decomposition characterization and architecture guards."""

from __future__ import annotations

import ast
import subprocess
import uuid
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.plans import CompiledPlan
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.gateway.mock import MockGateway
from product_factory.host.service import HostService
from product_factory.orchestration.composition import CompositionService
from product_factory.orchestration.finalization import RunFinalizer
from product_factory.orchestration.lifecycle import RunLifecycleEngine
from product_factory.scheduling.scheduler import WaveScheduler
from product_factory.workflows.artifacts import ArtifactLandSpec
from product_factory.workflows.base import WorkflowPack, execution_policy
from product_factory.workflows.handlers import register_pack_handler
from product_factory.workflows.handlers.base import AuthorityClass, ComposeContext
from product_factory.workflows.registry import (
    is_registered_workflow,
    register_workflow_pack,
    resolve_workflow_pack,
)


def test_coordinator_facade_delegates_to_lifecycle_engine() -> None:
    source = Path("src/product_factory/orchestration/coordinator.py").read_text(encoding="utf-8")
    assert "self._engine.run" in source
    assert "self._engine.resume" in source
    assert "RunLifecycleEngine" in source
    assert "_compose_architecture" not in source
    assert "self._engine.run" in source
    # Thin delegates for monkeypatch compatibility are allowed; bodies must not
    # implement task loops or composition.
    assert "def _execute_task(self, *args, **kwargs):" in source
    assert "return self._engine._execute_task(*args, **kwargs)" in source


def test_composition_service_deterministic_architecture() -> None:
    config = load_config()
    service = CompositionService(config=config, gateway=MockGateway())
    doc = service.compose_architecture("Build a cache", findings=[])
    assert "## Objective" in doc
    assert "Build a cache" in doc


def _task(task_id: str, *, deps: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        title=task_id,
        capability="documentation",
        objective="write",
        rationale="r",
        dependencies=deps or [],
        expected_output_schema="composition_result.v1",
        required_tool_classes=set(),
        acceptance_criteria=[
            AcceptanceCriterion(
                id=f"{task_id}-ac",
                description="done",
                verification="artifact_check",
                severity="blocking",
            )
        ],
        budget=TaskBudget(),
    )


def test_wave_scheduler_selects_ready_tasks() -> None:
    tasks = {"T1": _task("T1"), "T2": _task("T2", deps=["T1"])}
    plan = CompiledPlan(
        objective="o",
        assumptions=[],
        tasks=tasks,
        task_order=["T1", "T2"],
        final_artifacts=[],
        validation_strategy="deterministic",
        risk_classification="low",
        request_acceptance_criteria=[],
    )
    ready = WaveScheduler().select_ready(
        plan, {"T1": "pending", "T2": "pending"}, max_parallel=2
    )
    assert [t.id for t in ready] == ["T1"]


def test_run_request_rejects_unknown_workflow_type() -> None:
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RunRequest(
            request_id="r1",
            workflow_type="not_a_registered_pack",
            request_text="hello",
        )


def test_run_request_accepts_registered_alias() -> None:
    req = RunRequest(
        request_id="r1",
        workflow_type="code_change",
        request_text="hello",
    )
    assert req.workflow_type == "code_change"
    assert resolve_workflow_pack(req.workflow_type).id == "repository_change"


class _Sd2FixtureHandler:
    pack_id = "sd2-fixture-pack"

    def plan_template(self, request_text: str):
        from product_factory.domain.plans import FinalArtifactSpec, PlannerOutput

        return PlannerOutput(
            objective=request_text,
            assumptions=[],
            tasks=[
                TaskSpec(
                    id="T-001",
                    title="Compose fixture report",
                    capability="composition",
                    objective=request_text,
                    rationale="fixture",
                    dependencies=[],
                    expected_output_schema="composition_result.v1",
                    required_tool_classes={"artifact_write"},
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            id="T-001-AC1",
                            description="Report composed",
                            verification="artifact_check",
                            severity="blocking",
                        )
                    ],
                )
            ],
            final_artifacts=[
                FinalArtifactSpec(
                    logical_name="FIXTURE.md",
                    composer_task_id="T-001",
                    role="fixture_report",
                )
            ],
            validation_strategy="document",
            risk_classification="low",
            request_acceptance_criteria=[],
        )

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


def _ensure_fixture_pack() -> None:
    if is_registered_workflow("sd2-fixture-pack"):
        return
    role = "fixture_report"
    capabilities = frozenset({"composition"})
    pack = WorkflowPack(
        id="sd2-fixture-pack",
        version="1.0.0",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        allowed_capabilities=capabilities,
        default_planner_mode="fixed",
        execution_policy=execution_policy(
            capabilities=capabilities,
            validators=["document_sections"],
            output_roles=(role,),
            required_output_roles=frozenset({role}),
            fallback_composition_roles=frozenset({role}),
        ),
        artifacts=(
            ArtifactLandSpec(
                role=role,
                default_logical_name="FIXTURE.md",
                default_dest_path="docs/FIXTURE.md",
            ),
        ),
    )
    register_workflow_pack(pack)
    register_pack_handler(_Sd2FixtureHandler())


def test_fixture_pack_submits_via_public_host_api(tmp_path: Path) -> None:
    """G2 extensibility: register + HostService.submit without editing named lists."""
    _ensure_fixture_pack()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# fixture\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    config = load_config()
    host = HostService(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    request = RunRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        workflow_type="sd2-fixture-pack",
        request_text="Summarize the repository layout for SD2 fixture proof.",
        repository_path=repo,
        metadata={"allow_dirty_repo": "true"},
    )
    response = host.submit(request, mock=True, detach=False)
    assert response.ok, (response.code, response.message, response.details)
    assert response.data["workflow_type"] == "sd2-fixture-pack"
    host.close()


def test_pack_execution_policy_is_authoritative_for_finalizer() -> None:
    pack = resolve_workflow_pack("repository_change")
    finalizer = RunFinalizer()
    assert finalizer.pack_declares_patch_output(pack)
    assert not finalizer.pack_declares_architecture_output(pack)
    tech = resolve_workflow_pack("technical_plan")
    assert finalizer.pack_declares_architecture_output(tech)


def test_no_named_workflow_string_compare_in_shared_scheduler() -> None:
    source = Path("src/product_factory/scheduling/scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for child in ast.walk(node):
                if isinstance(child, ast.Constant) and child.value in {
                    "repository_change",
                    "technical_plan",
                    "code_change",
                    "architecture",
                }:
                    raise AssertionError(f"scheduler compares workflow name {child.value!r}")


def test_lifecycle_engine_module_exports_engine() -> None:
    assert RunLifecycleEngine is not None
