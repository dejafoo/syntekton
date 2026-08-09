"""Immutable lifecycle snapshots exchanged between owning services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product_factory.domain.plans import CompiledPlan
from product_factory.domain.runs import RunRequest
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.workflows.artifacts import ArtifactLandMap
from product_factory.workflows.base import WorkflowPack


@dataclass(frozen=True, slots=True)
class AdmittedRepository:
    base_commit: str | None
    summary: dict[str, Any] | None
    original_path: Path | None
    worktrees: WorktreeManager | None


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    run_id: str
    request: RunRequest
    status: str
    run_dir: Path
    workflow_pack: WorkflowPack | None
    land_map: ArtifactLandMap
    repository: AdmittedRepository


@dataclass(frozen=True, slots=True)
class PlanningOutcome:
    snapshot: RunSnapshot
    compiled_plan: CompiledPlan
    compiler_report_path: Path
    plan_artifact_sha256: str
    reused_existing: bool = False
