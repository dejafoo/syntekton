"""Composition root — construct application dependencies (SR2).

``RunLifecycleEngine`` sequences named owners; it must not construct brokers,
registries, or lifecycle services inline. Call ``build_application`` once and
inject the resulting ``ApplicationServices``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product_factory.application.command_service import LifecycleCommandService
from product_factory.config.loader import AppConfig
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.defaults import default_connector_registry
from product_factory.connectors.registry import ConnectorRegistry
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.composition.service import CompositionService
from product_factory.orchestration.finalization.run_finalizer import RunFinalizer
from product_factory.orchestration.task_preparation import TaskPreparationService
from product_factory.orchestration.validation_repair.service import ValidationRepairService
from product_factory.orchestration.wave_execution import WaveExecutionService
from product_factory.orchestration.worktree_lineage import WorktreeLineageService
from product_factory.persistence.database import Database
from product_factory.scheduling.scheduler import WaveScheduler
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.registry import ToolRegistry, default_tool_registry


@dataclass(slots=True)
class ApplicationServices:
    """Typed dependency graph constructed by the composition root."""

    config: AppConfig
    pf_root: Path
    db: Database
    skills: SkillRegistry
    tool_registry: ToolRegistry
    connector_registry: ConnectorRegistry
    connector_broker: ConnectorBroker
    raw_gateway: ModelGateway
    composition: CompositionService
    validation_repair: ValidationRepairService
    wave_scheduler: WaveScheduler
    worktree_lineage: WorktreeLineageService
    finalizer: RunFinalizer
    task_preparation: TaskPreparationService
    wave_execution: WaveExecutionService
    commands: LifecycleCommandService
    allow_deterministic_workers: bool = False
    use_deterministic_planner: bool = False
    # Bound by RunLifecycleEngine after construction (avoids circular init).
    lifecycle: Any | None = None


def build_application(
    *,
    config: AppConfig,
    gateway: ModelGateway,
    data_dir: Path | None = None,
    use_deterministic_planner: bool = False,
) -> ApplicationServices:
    """Construct the application dependency graph formerly built in engine.__init__."""

    allow_deterministic_workers = isinstance(gateway, MockGateway)
    deterministic_planner = use_deterministic_planner or isinstance(gateway, MockGateway)
    pf_root = data_dir or (config.root / ".product-factory")
    pf_root.mkdir(parents=True, exist_ok=True)
    db = Database(pf_root / "data" / "product_factory.sqlite")
    skills = SkillRegistry.load(config.root / "skills")
    tool_registry = default_tool_registry()
    connector_registry = default_connector_registry(
        config.connectors,
        config_root=config.root,
        deployment_state_path=pf_root / "deployments" / "staging-state.json",
    )
    # Connector tools share the one registry so ToolBroker.execute resolves
    # and trust-labels them exactly like built-in tools.
    for definition in connector_registry.tool_definitions():
        tool_registry.register(definition)
    connector_broker = ConnectorBroker(
        connector_registry,
        config=config.connectors,
        mock=isinstance(gateway, MockGateway),
    )
    # The provider adapter is immutable shared configuration. Instrumented
    # gateways are constructed per run and live only in RunExecutionContext.
    raw_gateway = gateway.inner if isinstance(gateway, InstrumentedModelGateway) else gateway
    composition = CompositionService(config=config, gateway=raw_gateway)
    validation_repair = ValidationRepairService(config=config, raw_gateway=raw_gateway)
    wave_scheduler = WaveScheduler()
    worktree_lineage = WorktreeLineageService()
    finalizer = RunFinalizer()
    task_preparation = TaskPreparationService(
        config=config,
        skills=skills,
        tool_registry=tool_registry,
        connector_registry=connector_registry,
        connector_broker=connector_broker,
    )
    wave_execution = WaveExecutionService(wave_scheduler=wave_scheduler)
    commands = LifecycleCommandService()
    return ApplicationServices(
        config=config,
        pf_root=pf_root,
        db=db,
        skills=skills,
        tool_registry=tool_registry,
        connector_registry=connector_registry,
        connector_broker=connector_broker,
        raw_gateway=raw_gateway,
        composition=composition,
        validation_repair=validation_repair,
        wave_scheduler=wave_scheduler,
        worktree_lineage=worktree_lineage,
        finalizer=finalizer,
        task_preparation=task_preparation,
        wave_execution=wave_execution,
        commands=commands,
        allow_deterministic_workers=allow_deterministic_workers,
        use_deterministic_planner=deterministic_planner,
    )
