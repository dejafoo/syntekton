"""Typed task preparation and runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from product_factory.context.assembler import AssembledContext
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskSpec
from product_factory.orchestration.composition.service import CompositionService
from product_factory.orchestration.effective_policy import EffectiveTaskPolicy
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.registry.capability_descriptors import CapabilityDescriptor
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.skills.registry import Skill
from product_factory.workflows.artifacts import ArtifactLandMap


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    access_mode: Literal["none", "read_only", "isolated_write"]
    root: Path
    base_revision: str
    inherited_artifact_instances: tuple[str, ...]
    lineage: tuple[dict[str, str], ...]
    pre_execution_patch_fingerprint: str | None
    original_repository: Path | None


@dataclass(frozen=True, slots=True)
class DependencyArtifact:
    producer_task_id: str
    role: str | None
    artifact_instance_id: str | None
    sha256: str
    media_type: str
    schema: str | None
    verified_excerpt: str | None


@dataclass(frozen=True, slots=True)
class TaskPreparationRequest:
    execution_session: RunExecutionContext
    run_request: RunRequest
    task: TaskSpec
    worktrees: WorktreeManager | None
    original_repository: Path | None
    base_commit: str
    dependency_outputs: tuple[dict[str, Any], ...] = ()
    land_map: ArtifactLandMap = field(default_factory=ArtifactLandMap)
    composition_role: str | None = None
    validation_evidence_refs: tuple[str, ...] = ()
    validator_results: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedTask:
    run_id: str
    run_request: RunRequest
    task_spec: TaskSpec
    descriptor: CapabilityDescriptor
    effective_policy: EffectiveTaskPolicy
    model_profile: str
    agent_profile: str
    matched_skills: tuple[Skill, ...]
    prompt_package: AssembledContext
    workspace: PreparedWorkspace
    dependency_artifacts: tuple[DependencyArtifact, ...]
    dependency_outputs: tuple[dict[str, Any], ...]
    repository_excerpts: tuple[dict[str, str], ...]
    registered_command_ids: tuple[str, ...]
    land_map: ArtifactLandMap
    composition_role: str | None
    validation_evidence_refs: tuple[str, ...]
    validator_results: tuple[dict[str, Any], ...]
    composition: CompositionService


@dataclass(frozen=True, slots=True)
class TaskRuntimeRequest:
    prepared_task: PreparedTask
    execution_session: RunExecutionContext
