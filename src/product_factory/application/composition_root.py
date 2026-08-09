"""The sole production composition root for Product Factory."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product_factory.application.command_service import LifecycleCommandService
from product_factory.application.ports import RunLifecyclePort
from product_factory.application.services import (
    ApplicationMetadata,
    ApplicationQueryService,
    ApplicationShutdownService,
    RunWorkerBackend,
    RunWorkerService,
)
from product_factory.config.loader import AppConfig
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.defaults import default_connector_registry
from product_factory.connectors.registry import ConnectorRegistry
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.composition.service import CompositionService
from product_factory.orchestration.finalization.run_finalizer import RunFinalizer
from product_factory.orchestration.lifecycle.admission import RunAdmissionService
from product_factory.orchestration.lifecycle.engine import RunLifecycleEngine
from product_factory.orchestration.lifecycle.session import RunExecutionSessionFactory
from product_factory.orchestration.planning import RunPlanningService
from product_factory.orchestration.task_preparation import TaskPreparationService
from product_factory.orchestration.task_runtime import TaskRuntimeService
from product_factory.orchestration.validation_repair.service import ValidationRepairService
from product_factory.orchestration.wave_execution import WaveExecutionService
from product_factory.orchestration.worktree_lineage import WorktreeLineageService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.database import Database
from product_factory.scheduling.scheduler import WaveScheduler
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.registry import ToolRegistry, default_tool_registry
from product_factory.workers.supervisor import WorkerSupervisor


@dataclass(frozen=True, slots=True)
class ProductFactoryApplication:
    """Complete immutable application surface shared by all host adapters."""

    commands: LifecycleCommandService
    queries: ApplicationQueryService
    workers: RunWorkerService
    lifecycle: RunLifecyclePort
    shutdown: ApplicationShutdownService
    metadata: ApplicationMetadata


# Compatibility name for callers that imported the incomplete SR2 container.
ApplicationServices = ProductFactoryApplication


def build_application(
    *,
    config: AppConfig,
    gateway: ModelGateway,
    data_dir: Path | None = None,
    use_deterministic_planner: bool = False,
) -> ProductFactoryApplication:
    """Construct every concrete production dependency exactly once."""

    allow_deterministic_workers = isinstance(gateway, MockGateway)
    deterministic_planner = use_deterministic_planner or isinstance(gateway, MockGateway)
    pf_root = data_dir or (config.root / ".product-factory")
    pf_root.mkdir(parents=True, exist_ok=True)

    database = Database(pf_root / "data" / "product_factory.sqlite")
    skills = SkillRegistry.load(config.root / "skills")
    tool_registry: ToolRegistry = default_tool_registry()
    connector_registry: ConnectorRegistry = default_connector_registry(
        config.connectors,
        config_root=config.root,
        deployment_state_path=pf_root / "deployments" / "staging-state.json",
    )
    for definition in connector_registry.tool_definitions():
        tool_registry.register(definition)
    connector_broker = ConnectorBroker(
        connector_registry,
        config=config.connectors,
        mock=allow_deterministic_workers,
    )
    raw_gateway = gateway.inner if isinstance(gateway, InstrumentedModelGateway) else gateway

    composition = CompositionService(config=config, gateway=raw_gateway)
    validation_repair = ValidationRepairService(config=config, raw_gateway=raw_gateway)
    wave_scheduler = WaveScheduler()
    worktree_lineage = WorktreeLineageService()
    finalizer = RunFinalizer()

    def record_artifact_instance(instance: ArtifactInstance) -> None:
        database.record_artifact_instance(instance.model_dump(mode="json"))

    task_preparation = TaskPreparationService(
        config=config,
        skills=skills,
        tool_registry=tool_registry,
        connector_registry=connector_registry,
        connector_broker=connector_broker,
        repository=database,
        worktree_lineage=worktree_lineage,
        on_artifact_instance=record_artifact_instance,
        composition=composition,
    )

    def approval_verify(
        request: Any,
        *,
        consumer_run_id: str,
        capability: str,
    ) -> bool:
        from product_factory.trust.approvals import verify_deployment_action_approval

        return verify_deployment_action_approval(
            database,
            request,
            consumer_run_id=consumer_run_id,
            capability=capability,
        )

    task_runtime = TaskRuntimeService(
        config=config,
        tool_registry=tool_registry,
        connector_broker=connector_broker,
        connector_registry=connector_registry,
        task_preparation=task_preparation,
        raw_gateway=raw_gateway,
        allow_deterministic_workers=allow_deterministic_workers,
        on_artifact_instance=record_artifact_instance,
        approval_verify=approval_verify,
    )
    wave_execution = WaveExecutionService(
        database=database,
        task_preparation=task_preparation,
        task_runtime=task_runtime,
        wave_scheduler=wave_scheduler,
    )

    def cancel_check(run_id: str) -> None:
        row = database.get_run(run_id)
        if row and int(row.get("cancel_requested") or 0):
            from product_factory.domain.errors import RunCancelledError

            raise RunCancelledError(f"Run {run_id} cancelled by operator")

    session_factory = RunExecutionSessionFactory(
        database=database,
        raw_gateway=raw_gateway,
        cancel_check=cancel_check,
    )
    admission = RunAdmissionService(config=config, database=database, data_root=pf_root)
    planning = RunPlanningService(
        config=config,
        database=database,
        skills=skills,
        use_deterministic_planner=deterministic_planner,
    )

    lifecycle = RunLifecycleEngine(
        config=config,
        pf_root=pf_root,
        db=database,
        skills=skills,
        tool_registry=tool_registry,
        connector_registry=connector_registry,
        connector_broker=connector_broker,
        raw_gateway=raw_gateway,
        composition=composition,
        validation_repair=validation_repair,
        worktree_lineage=worktree_lineage,
        finalizer=finalizer,
        task_preparation=task_preparation,
        task_runtime=task_runtime,
        wave_execution=wave_execution,
        session_factory=session_factory,
        admission=admission,
        planning=planning,
        allow_deterministic_workers=allow_deterministic_workers,
        use_deterministic_planner=deterministic_planner,
    )
    commands = LifecycleCommandService(lifecycle=lifecycle)
    queries = ApplicationQueryService(database, data_root=pf_root, config=config)
    worker_backend = RunWorkerBackend(
        config=config,
        gateway=raw_gateway,
        data_root=pf_root,
        database=database,
        commands=commands,
    )
    supervisor = WorkerSupervisor(
        db=database,
        execute=worker_backend.execute,
        resume=commands.resume,
        worktree_key=worker_backend.worktree_key,
        on_error=worker_backend.on_error,
        lease_ttl_seconds=float(os.environ.get("PRODUCT_FACTORY_WORKER_LEASE_TTL", "30")),
        heartbeat_seconds=float(os.environ.get("PRODUCT_FACTORY_WORKER_HEARTBEAT_SECONDS", "10")),
        scan_seconds=float(os.environ.get("PRODUCT_FACTORY_WORKER_SCAN_SECONDS", "5")),
    )
    workers = RunWorkerService(backend=worker_backend, supervisor=supervisor)
    shutdown = ApplicationShutdownService(workers=workers, database=database)
    metadata = ApplicationMetadata(
        data_root=pf_root,
        configuration_root=config.root,
        deterministic_planner=deterministic_planner,
        deterministic_workers=allow_deterministic_workers,
    )
    return ProductFactoryApplication(
        commands=commands,
        queries=queries,
        workers=workers,
        lifecycle=lifecycle,
        shutdown=shutdown,
        metadata=metadata,
    )


def build_host_service(
    *,
    config: AppConfig,
    gateway: ModelGateway,
    data_dir: Path | None = None,
    use_deterministic_planner: bool = False,
    observe_base_url: str | None = None,
):
    """Construct the host adapter from the one completed application."""

    from product_factory.host.service import HostService

    application = build_application(
        config=config,
        gateway=gateway,
        data_dir=data_dir,
        use_deterministic_planner=use_deterministic_planner,
    )
    return HostService(application=application, observe_base_url=observe_base_url)


def build_coordinator(
    *,
    config: AppConfig,
    gateway: ModelGateway,
    data_dir: Path | None = None,
    use_deterministic_planner: bool = False,
):
    """Build the legacy coordinator adapter from the application root."""

    from product_factory.orchestration.coordinator import RunCoordinator

    application = build_application(
        config=config,
        gateway=gateway,
        data_dir=data_dir,
        use_deterministic_planner=use_deterministic_planner,
    )
    return RunCoordinator(
        lifecycle=application.lifecycle,
        commands=application.commands,
        queries=application.queries,
    )
