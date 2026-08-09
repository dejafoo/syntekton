"""SR2 — lifecycle extraction characterization and import-boundary guards."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock

from product_factory.application import (
    ApplicationServices,
    LifecycleCommandService,
    build_application,
    build_coordinator,
    build_host_service,
)
from product_factory.application.composition_root import ApplicationServices as RootServices
from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.composition.input import (
    CompositionInput,
    composition_input_from_compose_context,
)
from product_factory.orchestration.task_preparation import TaskPreparationService
from product_factory.orchestration.task_runtime import TaskRuntimeService
from product_factory.orchestration.wave_execution import WaveExecutionService
from product_factory.workflows.handlers.base import ComposeContext
from tests.conftest import hermetic_validation_config

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "product_factory"


def test_application_services_and_build_application_exist(tmp_path: Path) -> None:
    assert ApplicationServices is RootServices
    config = load_config()
    services = build_application(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    assert isinstance(services, ApplicationServices)
    assert services.queries.database is not None
    assert services.workers.supervisor.db is services.queries.database
    assert isinstance(services.commands, LifecycleCommandService)
    assert services.commands.lifecycle is services.lifecycle
    assert services.metadata.data_root == tmp_path / "pf"


def test_task_preparation_and_wave_execution_modules_exist() -> None:
    assert (SRC / "orchestration" / "task_preparation.py").is_file()
    assert (SRC / "orchestration" / "task_runtime.py").is_file()
    assert (SRC / "orchestration" / "wave_execution.py").is_file()
    assert TaskPreparationService is not None
    assert TaskRuntimeService is not None
    assert WaveExecutionService is not None
    assert hasattr(TaskPreparationService, "prepare_effective_policy")
    assert hasattr(TaskPreparationService, "assemble_execution_request")
    assert hasattr(TaskRuntimeService, "execute")
    assert hasattr(WaveExecutionService, "select_ready")
    assert hasattr(WaveExecutionService, "run_wave_cycle")


def test_engine_must_not_construct_tool_broker() -> None:
    """G2: ToolBroker construction belongs to TaskRuntimeService, not the engine body."""
    source = (SRC / "orchestration" / "lifecycle" / "engine.py").read_text(encoding="utf-8")
    assert "ToolBroker(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "ToolBroker":
                raise AssertionError("engine.py must not call ToolBroker(...)")
            if isinstance(func, ast.Attribute) and func.attr == "ToolBroker":
                raise AssertionError("engine.py must not call ToolBroker(...)")


def test_lifecycle_command_service_delegates_without_reimplementing() -> None:
    engine = MagicMock()
    engine.run.return_value = "manifest"
    engine.resume.return_value = "resumed"
    engine.cancel.return_value = {"status": "cancelled"}
    engine.revise.return_value = "revised"
    engine.approve.return_value = {"status": "approved"}
    engine.reject.return_value = {"status": "rejected"}
    engine.apply_patch.return_value = {"applied": True}

    commands = LifecycleCommandService(lifecycle=engine)
    request = MagicMock(spec=RunRequest)

    assert commands.submit(request, run_id="run-1") == "manifest"
    engine.run.assert_called_once_with(request, run_id="run-1")
    assert commands.resume("run-1") == "resumed"
    engine.resume.assert_called_once_with("run-1")
    assert commands.cancel("run-1")["status"] == "cancelled"
    engine.cancel.assert_called_once_with("run-1")
    assert commands.revise("run-1", note="n") == "revised"
    engine.revise.assert_called_once_with("run-1", note="n")
    assert commands.approve("run-1", apply=True)["status"] == "approved"
    engine.approve.assert_called_once_with("run-1", apply=True)
    assert commands.reject("run-1")["status"] == "rejected"
    engine.reject.assert_called_once_with("run-1")
    assert commands.apply("run-1")["applied"] is True
    engine.apply_patch.assert_called_once_with("run-1")

    source = (SRC / "application" / "command_service.py").read_text(encoding="utf-8")
    # Thin facade: no persistence / approval reinterpretation.
    assert "upsert_run" not in source
    assert "ApprovalBlockedError" not in source
    assert "json.loads" not in source


def test_composition_input_is_immutable_typed_boundary() -> None:
    assert (SRC / "orchestration" / "composition" / "input.py").is_file()
    payload = CompositionInput(
        run_snapshot={"run_id": "run-1"},
        compiled_plan={"tasks": []},
        task_results=({"task_id": "t1"},),
        dependency_artifacts=({"role": "patch"},),
        validation_evidence=("ref-1",),
        findings=({"severity": "x"},),
        lineage={"parent": None},
        effective_policy={"mode": "live"},
    )
    assert payload.run_snapshot["run_id"] == "run-1"
    try:
        payload.run_snapshot = {"run_id": "other"}  # type: ignore[misc]
        raise AssertionError("CompositionInput must be frozen")
    except FrozenInstanceError:
        pass

    ctx = ComposeContext(
        request=MagicMock(spec=RunRequest),
        role="architecture_document",
        document_name="ARCHITECTURE.md",
        findings=[{"id": "f1"}],
        dependency_outputs=[{"artifact": "a"}],
        run_id="run-9",
        profile="writer",
        validation_evidence_refs=["v1"],
        validator_results=[{"ok": True}],
    )
    adapted = composition_input_from_compose_context(ctx)
    assert adapted.run_snapshot is not None
    assert adapted.run_snapshot["run_id"] == "run-9"
    assert adapted.findings == ({"id": "f1"},)
    assert adapted.dependency_artifacts == ({"artifact": "a"},)
    assert "v1" in adapted.validation_evidence
    assert {"ok": True} in adapted.validation_evidence
    ctx.composition_input = adapted
    assert ctx.composition_input is adapted


def test_run_coordinator_remains_thin_facade() -> None:
    source = (SRC / "orchestration" / "coordinator.py").read_text(encoding="utf-8")
    assert "self._lifecycle.run" in source
    assert "build_application" not in source
    assert "_compose_architecture" not in source
    assert "def _execute_task" not in source
    assert "def __getattr__" not in source
    tree = ast.parse(source)
    class_defs = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RunCoordinator"
    ]
    assert len(class_defs) == 1
    method_names = {
        n.name for n in class_defs[0].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    # Public mutations stay thin delegates.
    for name in ("run", "resume", "approve", "reject", "cancel", "revise", "apply_patch"):
        assert name in method_names


def test_engine_is_constructed_only_by_application_root(tmp_path: Path) -> None:
    from product_factory.orchestration.lifecycle.engine import RunLifecycleEngine

    config = load_config()
    services = build_application(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    assert isinstance(services.lifecycle, RunLifecycleEngine)
    assert services.commands.lifecycle is services.lifecycle
    engine_source = (SRC / "orchestration" / "lifecycle" / "engine.py").read_text(encoding="utf-8")
    assert "build_application" not in engine_source
    assert "ApplicationServices" not in engine_source


def test_coordinator_public_api_unchanged(tmp_path: Path) -> None:
    config = load_config()
    coord = build_coordinator(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    assert hasattr(coord, "run")
    assert hasattr(coord, "resume")
    assert hasattr(coord, "approve")
    assert hasattr(coord, "reject")
    assert hasattr(coord, "cancel")
    assert hasattr(coord, "revise")
    assert hasattr(coord, "apply_patch")
    assert isinstance(coord.commands, LifecycleCommandService)
    assert coord.commands.lifecycle is coord._lifecycle
    assert coord.queries.database is not None
    assert not hasattr(coord, "_engine")


def _module_imports_name(path: Path, banned: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == banned or alias.name.startswith(f"{banned}."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == banned or module.startswith(f"{banned}."):
                return True
            if (
                module.endswith("coordinator")
                or module == "product_factory.orchestration.coordinator"
            ):
                for alias in node.names:
                    if alias.name == "RunCoordinator":
                        return True
    return False


def test_new_sr2_modules_must_not_import_run_coordinator() -> None:
    banned_files = [
        SRC / "application" / "command_service.py",
        SRC / "application" / "__init__.py",
        SRC / "orchestration" / "task_preparation.py",
        SRC / "orchestration" / "task_runtime.py",
        SRC / "orchestration" / "wave_execution.py",
        SRC / "orchestration" / "composition" / "input.py",
    ]
    for path in banned_files:
        assert path.is_file(), path
        assert not _module_imports_name(path, "product_factory.orchestration.coordinator"), (
            f"{path} must not import RunCoordinator / coordinator"
        )
        source = path.read_text(encoding="utf-8")
        assert "RunCoordinator" not in source, f"{path} mentions RunCoordinator"
    root_source = (SRC / "application" / "composition_root.py").read_text(encoding="utf-8")
    assert "from product_factory.orchestration.coordinator import RunCoordinator" in root_source


def _engine_imports_from(module_prefix: str) -> list[str]:
    """Return ImportFrom module names under ``module_prefix`` (exact or child)."""
    engine = SRC / "orchestration" / "lifecycle" / "engine.py"
    tree = ast.parse(engine.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == module_prefix or module.startswith(f"{module_prefix}."):
                found.append(module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_prefix or alias.name.startswith(f"{module_prefix}."):
                    found.append(alias.name)
    return found


def test_engine_must_not_import_concrete_executor_modules() -> None:
    """G2: capability executors stay behind execute_task / TaskRuntimeService."""
    # Allow the package-level execute_task entrypoint only (not concrete classes).
    allowed = {
        "product_factory.executors",
        "product_factory.executors.protocol",
    }
    imported = _engine_imports_from("product_factory.executors")
    banned = [mod for mod in imported if mod not in allowed]
    assert not banned, (
        "engine.py must not import concrete executor modules "
        f"(e.g. validation/composition); found {banned}"
    )
    source = (SRC / "orchestration" / "lifecycle" / "engine.py").read_text(encoding="utf-8")
    for concrete in (
        "TestExecutionExecutor",
        "CompositionExecutor",
        "ValidationRepairExecutor",
        "from product_factory.executors.validation",
        "from product_factory.executors.composition",
        "from product_factory.executors.interface_agent",
    ):
        assert concrete not in source, f"engine.py must not reference {concrete}"


def test_engine_temporary_forbidden_dependency_exceptions() -> None:
    """Document remaining SR2.F exceptions with removal issue ids.

    Temporary exceptions (must shrink toward zero):
    - ``handler_for`` / workflow handlers — finalization compose fallback still
      resolves pack handlers in-engine
      (issue: remove-engine-handler-for-2026-08).
    - ``ApprovalService`` — not currently imported; approve/reject still use
      file-backed approval.json pending trust-service ownership
      (issue: remove-coordinator-approval-verify-2026-08).
    """
    source = (SRC / "orchestration" / "lifecycle" / "engine.py").read_text(encoding="utf-8")
    # ApprovalService must stay out of the engine (target met; guard regression).
    assert "ApprovalService" not in source
    assert "product_factory.trust.approvals" not in source

    # handler_for is an acknowledged temporary exception until finalization owns
    # pack-handler resolution (issue: remove-engine-handler-for-2026-08).
    temporary_handler_for = "from product_factory.workflows.handlers import handler_for"
    assert temporary_handler_for in source, (
        "expected temporary handler_for import; update this guard if ownership moved"
    )


def test_engine_production_compose_attaches_composition_input() -> None:
    """Engine fallback compose path must populate ComposeContext.composition_input."""
    source = (SRC / "orchestration" / "lifecycle" / "engine.py").read_text(encoding="utf-8")
    assert "composition_input_from_compose_context" in source
    assert "compose_ctx.composition_input" in source
    executor = (SRC / "executors" / "composition.py").read_text(encoding="utf-8")
    assert "composition_input_from_compose_context" in executor
    assert "compose_ctx.composition_input" in executor


def test_mock_quality_gate_characterization_completes(tmp_path: Path) -> None:
    """Fresh mock quality_gate run through coordinator/commands graph."""
    from decimal import Decimal

    from product_factory.domain.budgets import RunBudget
    from tests.conftest import clone_fixture

    fixture = clone_fixture(ROOT / "tests" / "fixtures" / "sample_api", tmp_path / "repo")
    coord = build_coordinator(
        config=hermetic_validation_config(ROOT),
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    manifest = coord.commands.submit(
        RunRequest(
            request_id="req-sr2-qg",
            workflow_type="quality_gate",
            request_text="Assess test coverage and quality risk in the sample API.",
            repository_path=fixture,
            budget=RunBudget(max_cost_usd=Decimal("3.00")),
            approval_policy="none",
            metadata={"disable_review": "true"},
        )
    )
    assert manifest.final_status == "completed", manifest.notes
    assert manifest.metadata.get("workflow_pack_id") == "quality_gate"
    output = tmp_path / "pf" / "runs" / manifest.run_id / "output"
    assert (output / "TEST_PLAN.md").is_file()
    assert (output / "QUALITY_FINDINGS.md").is_file()
    assert (output / "SECURITY_EVIDENCE.md").is_file()


def test_cancel_queued_run_via_commands(tmp_path: Path) -> None:
    """Cancel after submit while still queued (immediate terminal)."""
    from decimal import Decimal

    from product_factory.domain.budgets import RunBudget

    coord = build_coordinator(
        config=load_config(),
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    request = RunRequest(
        request_id="req-sr2-cancel",
        workflow_type="change_intake",
        request_text="Clarify an ambiguous intake request before planning.",
        budget=RunBudget(max_cost_usd=Decimal("2.00")),
        approval_policy="none",
    )
    run_id = "run-sr2-cancel-queued"
    coord.queries.database.upsert_run(
        run_id=run_id,
        workflow_type=request.workflow_type,
        status="queued",
        request=request.model_dump(mode="json"),
        active_operation="queued",
    )
    result = coord.commands.cancel(run_id)
    assert result["status"] == "cancelled"
    assert result.get("cancel_requested") is True
    row = coord.queries.database.get_run(run_id)
    assert row is not None
    assert row["status"] == "cancelled"


def test_host_service_mutations_use_commands(tmp_path: Path) -> None:
    """HostService public mutations route through LifecycleCommandService."""
    service = build_host_service(
        config=load_config(),
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    assert service.commands is service.application.commands
    assert service.supervisor is service.application.workers.supervisor
    source = (SRC / "host" / "service.py").read_text(encoding="utf-8")
    for needle in (
        "self.commands.approve",
        "self.commands.reject",
        "self.commands.resume",
        "self.commands.apply",
        "self.commands.cancel",
        "self.commands.revise",
    ):
        assert needle in source, f"HostService must use {needle}"
    worker_source = (SRC / "application" / "services.py").read_text(encoding="utf-8")
    assert "self.commands.submit" in worker_source
    for banned in (
        "self.coord.approve",
        "self.coord.reject",
        "self.coord.resume(",
        "self.coord.apply_patch",
        "self.coord.cancel",
        "self.coord.revise",
        "self.coord.run(",
        "resume=self.coord.resume",
    ):
        assert banned not in source, f"HostService must not call {banned}"
