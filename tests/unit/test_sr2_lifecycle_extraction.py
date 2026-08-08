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
)
from product_factory.application.composition_root import ApplicationServices as RootServices
from product_factory.config.loader import load_config
from product_factory.domain.runs import RunRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.composition.input import (
    CompositionInput,
    composition_input_from_compose_context,
)
from product_factory.orchestration.coordinator import RunCoordinator
from product_factory.orchestration.task_preparation import TaskPreparationService
from product_factory.orchestration.wave_execution import WaveExecutionService
from product_factory.workflows.handlers.base import ComposeContext

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
    assert services.db is not None
    assert services.skills is not None
    assert services.tool_registry is not None
    assert services.connector_broker is not None
    assert services.composition is not None
    assert services.validation_repair is not None
    assert services.wave_scheduler is not None
    assert services.worktree_lineage is not None
    assert services.finalizer is not None
    assert isinstance(services.task_preparation, TaskPreparationService)
    assert isinstance(services.wave_execution, WaveExecutionService)
    assert services.wave_execution.wave_scheduler is services.wave_scheduler
    assert isinstance(services.commands, LifecycleCommandService)
    assert services.lifecycle is None  # unbound until engine construction


def test_task_preparation_and_wave_execution_modules_exist() -> None:
    assert (SRC / "orchestration" / "task_preparation.py").is_file()
    assert (SRC / "orchestration" / "wave_execution.py").is_file()
    assert TaskPreparationService is not None
    assert WaveExecutionService is not None
    assert hasattr(TaskPreparationService, "prepare_effective_policy")
    assert hasattr(TaskPreparationService, "assemble_execution_request")
    assert hasattr(WaveExecutionService, "select_ready")


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


def test_run_coordinator_remains_thin_facade() -> None:
    source = (SRC / "orchestration" / "coordinator.py").read_text(encoding="utf-8")
    assert "self._engine.run" in source
    assert "return self._engine._execute_task(*args, **kwargs)" in source
    assert "build_application" in source
    # Must not re-absorb task loops or composition.
    assert "_compose_architecture" not in source
    assert "def _execute_task(self, *args, **kwargs):" in source
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


def test_engine_accepts_prebuilt_application_services(tmp_path: Path) -> None:
    from product_factory.orchestration.lifecycle.engine import RunLifecycleEngine

    config = load_config()
    services = build_application(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        use_deterministic_planner=True,
    )
    engine = RunLifecycleEngine(
        config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / "pf",
        services=services,
    )
    assert engine.task_preparation is services.task_preparation
    assert engine.wave_execution is services.wave_execution
    assert engine.db is services.db
    assert services.lifecycle is engine
    assert engine.commands is services.commands
    assert engine.commands.lifecycle is engine


def test_coordinator_public_api_unchanged(tmp_path: Path) -> None:
    config = load_config()
    coord = RunCoordinator(
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
    assert coord.task_preparation is coord._engine.task_preparation
    assert coord.wave_execution is coord._engine.wave_execution
    assert isinstance(coord.commands, LifecycleCommandService)
    assert coord.commands.lifecycle is coord._engine


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
        SRC / "application" / "composition_root.py",
        SRC / "application" / "command_service.py",
        SRC / "application" / "__init__.py",
        SRC / "orchestration" / "task_preparation.py",
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
