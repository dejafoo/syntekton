"""WaveExecutionService — runnable selection and wave-cycle ownership (SR2).

Ownership boundary
------------------
* ``WaveScheduler`` remains decision-only: ready-task selection, concurrency
  slot accounting, and transitive dependency walks.
* ``WaveExecutionService`` owns the wave-cycle orchestration surface that the
  lifecycle engine sequences: select ready, build dependency outputs, mark
  tasks running, dispatch via ``run_wave``, and return collected results.
  Validation/repair result processing stays sequenced by the lifecycle engine
  for this cut.
* After G2, this service also owns the SR3 task-completion durability pair:
  terminal task upsert + corresponding observability event via ``UnitOfWork``.

Do not import the compatibility facade or grow scheduling decisions here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_factory.domain.artifacts import ResourceRef
from product_factory.domain.errors import BudgetExhaustedError, RuntimeFailureError
from product_factory.domain.plans import CompiledPlan
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import (
    TaskResult,
    TaskSpec,
    is_terminal_task_status,
    requires_repair_or_terminal_resolution,
)
from product_factory.observability.contracts import EventSeverity, ObservabilityEvent
from product_factory.observability.ids import new_event_id
from product_factory.orchestration.concurrency import run_wave
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.orchestration.task_contracts import TaskPreparationRequest, TaskRuntimeRequest
from product_factory.orchestration.task_preparation import (
    BlockedPreparation,
    TaskPreparationService,
)
from product_factory.orchestration.task_runtime import TaskRuntimeService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.scheduling.scheduler import WaveScheduler
from product_factory.workflows.artifacts import ArtifactLandMap


@dataclass(frozen=True, slots=True)
class WaveAdvanceRequest:
    """Authoritative inputs needed to advance exactly one wave."""

    execution_session: RunExecutionContext
    request: RunRequest
    plan: CompiledPlan
    task_status: dict[str, str]
    prior_results: tuple[TaskResult, ...]
    spent: Decimal
    worktrees: WorktreeManager | None
    original_repository: Path | None
    base_commit: str
    land_map: ArtifactLandMap
    composition_roles: dict[str, str]
    validation_evidence_refs: tuple[str, ...]
    validator_results: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class WaveAdvanceOutcome:
    """Deterministically ordered result of one wave advancement."""

    ready: tuple[TaskSpec, ...]
    wave_results: tuple[TaskResult, ...]
    dependency_outputs_by_task: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    finalization_eligible: bool = False


class WaveExecutionService:
    """Wave-cycle owner; scheduler stays the decision authority."""

    def __init__(
        self,
        *,
        database: Database,
        task_preparation: TaskPreparationService,
        task_runtime: TaskRuntimeService,
        wave_scheduler: WaveScheduler | None = None,
    ) -> None:
        self.database = database
        self.task_preparation = task_preparation
        self.task_runtime = task_runtime
        self.wave_scheduler = wave_scheduler or WaveScheduler()

    def execute_task(
        self,
        *,
        execution_session: RunExecutionContext,
        request: RunRequest,
        task: TaskSpec,
        worktrees: WorktreeManager | None,
        original_repository: Path | None,
        base_commit: str,
        dependency_outputs: list[dict[str, Any]],
        land_map: ArtifactLandMap,
        composition_role: str | None,
        validation_evidence_refs: list[str],
        validator_results: list[dict[str, Any]],
    ) -> TaskResult:
        """Prepare, dispatch, and persist one task outcome for the active wave."""

        prepared = self.task_preparation.prepare(
            TaskPreparationRequest(
                execution_session=execution_session,
                run_request=request,
                task=task,
                worktrees=worktrees,
                original_repository=original_repository,
                base_commit=base_commit,
                dependency_outputs=tuple(dependency_outputs),
                land_map=land_map,
                composition_role=composition_role,
                validation_evidence_refs=tuple(validation_evidence_refs),
                validator_results=tuple(validator_results),
            )
        )
        if isinstance(prepared, BlockedPreparation):
            self.record_task_completion(
                db=self.database,
                run_id=execution_session.run_id,
                task=task,
                result=prepared.result,
            )
            return prepared.result

        runtime_outcome = self.task_runtime.execute(
            TaskRuntimeRequest(prepared_task=prepared, execution_session=execution_session)
        )
        result = runtime_outcome.result
        if not result.evidence_refs:
            result.evidence_refs = [
                ResourceRef(
                    id=f"context:{task.id}:{excerpt['path']}",
                    resource_type="file",
                    origin="task",
                    scope=excerpt["path"],
                    trust_level="mixed",
                    content_hash=hashlib.sha256(excerpt["content"].encode()).hexdigest(),
                )
                for excerpt in prepared.repository_excerpts
            ]
        if not result.provider:
            gateway = execution_session.gateway
            result.provider = getattr(gateway, "default_model", type(gateway).__name__)
        if not result.prompt_package_hash:
            result.prompt_package_hash = prepared.prompt_package.package_hash

        self.record_task_completion(
            db=self.database,
            run_id=execution_session.run_id,
            task=task,
            result=result,
        )
        for artifact in result.artifact_refs:
            self.database.record_artifact(artifact.model_dump(mode="json"))
            self.database.record_artifact_instance(
                ArtifactInstance.create(
                    run_id=execution_session.run_id,
                    sha256=artifact.sha256,
                    content_class="durable_output",
                    capture_level=execution_session.recorder.capture_level,
                    role=artifact.logical_name,
                    producer_task_id=task.id,
                    media_type=artifact.media_type,
                    schema_id=artifact.schema_id,
                    schema_version=artifact.schema_version,
                    size_bytes=artifact.size_bytes,
                    display_name=artifact.logical_name,
                ).model_dump(mode="json")
            )
            execution_session.recorder.emit(
                run_id=execution_session.run_id,
                event_type="artifact.created",
                task_id=task.id,
                summary=artifact.logical_name,
                payload=artifact.model_dump(mode="json"),
            )
        for tool_call in runtime_outcome.tool_call_records:
            self.database.record_tool_call(
                run_id=execution_session.run_id,
                record=tool_call.model_dump(mode="json"),
            )
        return result

    def select_ready(
        self,
        plan: CompiledPlan,
        task_status: dict[str, str],
        *,
        max_parallel: int,
    ) -> list[TaskSpec]:
        """Delegate runnable selection to WaveScheduler."""
        return self.wave_scheduler.select_ready(plan, task_status, max_parallel=max_parallel)

    def transitive_dependencies(self, plan: CompiledPlan, task_id: str) -> set[str]:
        return self.wave_scheduler.transitive_dependencies(plan, task_id)

    def build_dependency_outputs(
        self,
        *,
        plan: CompiledPlan,
        ready: list[TaskSpec],
        prior_results: list[TaskResult],
        artifacts: ArtifactStore,
    ) -> dict[str, list[dict[str, Any]]]:
        """Precompute dependency context for each ready task from prior results."""
        return {
            task.id: [
                {
                    "task_id": prior.task_id,
                    "dependencies": plan.tasks[prior.task_id].dependencies,
                    "summary": prior.summary,
                    "artifact_refs": [ref.model_dump(mode="json") for ref in prior.artifact_refs],
                    "artifact_excerpts": [
                        {
                            "logical_name": ref.logical_name,
                            "sha256": ref.sha256,
                            "content": artifacts.get_text(ref.sha256)[:12_000],
                        }
                        for ref in prior.artifact_refs
                        if ref.media_type.startswith("text/")
                        or ref.media_type == "application/json"
                    ],
                    "findings": [finding.model_dump(mode="json") for finding in prior.findings],
                }
                for prior in prior_results
                if prior.task_id in self.transitive_dependencies(plan, task.id)
            ]
            for task in ready
        }

    def advance(self, advance_request: WaveAdvanceRequest) -> WaveAdvanceOutcome:
        """Advance exactly one wave without lifecycle callbacks."""

        session = advance_request.execution_session
        request = advance_request.request
        plan = advance_request.plan
        task_status = advance_request.task_status
        session.cancel_check()
        if advance_request.spent >= request.budget.max_cost_usd:
            raise BudgetExhaustedError("Run budget exhausted before wave")
        session.ledger.check_wall_clock()

        ready = self.select_ready(
            plan,
            task_status,
            max_parallel=request.budget.max_parallel_tasks,
        )
        if not ready:
            if all(is_terminal_task_status(task_status[task_id]) for task_id in plan.tasks):
                return WaveAdvanceOutcome(ready=(), wave_results=(), finalization_eligible=True)
            pending = [task_id for task_id, status in task_status.items() if status == "pending"]
            failed = [
                f"{result.task_id}: {result.summary}"
                for result in advance_request.prior_results
                if requires_repair_or_terminal_resolution(result.status)
            ]
            if failed:
                raise RuntimeFailureError("Dependency failed; " + "; ".join(failed))
            raise RuntimeFailureError(f"Unsatisfiable dependencies for tasks: {pending}")

        dependency_outputs_by_task = self.build_dependency_outputs(
            plan=plan,
            ready=ready,
            prior_results=list(advance_request.prior_results),
            artifacts=session.artifacts,
        )
        for task in ready:
            task_status[task.id] = "running"
            self.database.upsert_task(
                run_id=session.run_id,
                task_id=task.id,
                capability=task.capability,
                status="running",
                spec=task.model_dump(mode="json"),
                started_at=datetime.now(UTC).isoformat(),
                active_operation=task.capability,
            )
            session.recorder.emit(
                run_id=session.run_id,
                event_type="task.started",
                task_id=task.id,
                summary=f"Task {task.id} started",
                payload={"task_id": task.id, "capability": task.capability, "title": task.title},
            )

        def _run_one(task: TaskSpec) -> TaskResult:
            return self.execute_task(
                execution_session=session,
                request=request,
                task=task,
                worktrees=advance_request.worktrees,
                original_repository=advance_request.original_repository,
                base_commit=advance_request.base_commit,
                dependency_outputs=dependency_outputs_by_task.get(task.id, []),
                land_map=advance_request.land_map,
                composition_role=advance_request.composition_roles.get(task.id),
                validation_evidence_refs=list(advance_request.validation_evidence_refs),
                validator_results=list(advance_request.validator_results),
            )

        wave_results = run_wave(
            ready,
            executor_fn=_run_one,
            max_workers=request.budget.max_parallel_tasks,
        )
        session.cancel_check()
        return WaveAdvanceOutcome(
            ready=tuple(ready),
            wave_results=tuple(wave_results),
            dependency_outputs_by_task=dependency_outputs_by_task,
        )

    @staticmethod
    def record_task_completion(
        *,
        db: Database,
        run_id: str,
        task: TaskSpec,
        result: TaskResult,
        ended_at: str | None = None,
        attempt: int | None = None,
        effective_policy: dict[str, Any] | None = None,
    ) -> int:
        """Persist terminal task state and its event in one unit of work.

        Couples ``tasks`` upsert with the ``task.completed`` / ``task.failed``
        durable event. Callers may emit additional telemetry (budget, artifacts)
        only after this commit returns.
        """
        ended = ended_at or datetime.now(UTC).isoformat()
        event_type = "task.completed" if result.status == "success" else "task.failed"
        severity = EventSeverity.INFO if result.status == "success" else EventSeverity.ERROR
        event = ObservabilityEvent(
            event_id=new_event_id(),
            type=event_type,
            run_id=run_id,
            task_id=task.id,
            severity=severity,
            summary=f"Task {task.id} {result.status}",
            payload={
                "task_id": task.id,
                "status": result.status,
                "summary": result.summary,
                "usage": result.usage.model_dump(mode="json"),
            },
        )
        return db.unit_of_work().complete_task_with_event(
            run_id=run_id,
            task_id=task.id,
            capability=task.capability,
            status=result.status,
            spec=task.model_dump(mode="json"),
            result=result.model_dump(mode="json"),
            ended_at=ended,
            attempt=attempt,
            active_operation=None,
            effective_policy=effective_policy,
            event=event,
        )
