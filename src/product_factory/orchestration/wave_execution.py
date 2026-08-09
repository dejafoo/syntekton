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

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from product_factory.domain.plans import CompiledPlan
from product_factory.domain.tasks import TaskResult, TaskSpec
from product_factory.observability.contracts import EventSeverity, ObservabilityEvent
from product_factory.observability.ids import new_event_id
from product_factory.orchestration.concurrency import run_wave
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.scheduling.scheduler import WaveScheduler

TaskWaveExecutor = Callable[[TaskSpec, list[dict[str, Any]]], TaskResult]
MarkTaskStarted = Callable[[TaskSpec], None]
BeforeDispatch = Callable[[], None]


@dataclass(frozen=True, slots=True)
class WaveCycleResult:
    """One wave-cycle outcome for the lifecycle engine to process."""

    ready: list[TaskSpec]
    wave_results: list[TaskResult]
    dependency_outputs_by_task: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


class WaveExecutionService:
    """Wave-cycle owner; scheduler stays the decision authority."""

    def __init__(self, *, wave_scheduler: WaveScheduler | None = None) -> None:
        self.wave_scheduler = wave_scheduler or WaveScheduler()

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

    def run_wave_cycle(
        self,
        *,
        plan: CompiledPlan,
        task_status: dict[str, str],
        max_parallel: int,
        prior_results: list[TaskResult],
        artifacts: ArtifactStore,
        mark_task_started: MarkTaskStarted,
        executor_fn: TaskWaveExecutor,
        before_dispatch: BeforeDispatch | None = None,
    ) -> WaveCycleResult:
        """Select ready tasks, mark running, dispatch the wave, collect results.

        When no tasks are ready, returns an empty cycle so the lifecycle engine
        can diagnose deadlock / terminal completion. Does not mutate results
        beyond ``task_status`` transitions to ``running`` for dispatched tasks.
        """
        ready = self.select_ready(plan, task_status, max_parallel=max_parallel)
        if not ready:
            return WaveCycleResult(ready=[], wave_results=[], dependency_outputs_by_task={})

        if before_dispatch is not None:
            before_dispatch()

        # Same-wave tasks never depend on each other (enforced by runnable
        # selection), so dependency context is safely precomputed from the
        # pre-wave results snapshot.
        dependency_outputs_by_task = self.build_dependency_outputs(
            plan=plan,
            ready=ready,
            prior_results=prior_results,
            artifacts=artifacts,
        )
        for task in ready:
            task_status[task.id] = "running"
            mark_task_started(task)

        def _run_one(task: TaskSpec) -> TaskResult:
            return executor_fn(task, dependency_outputs_by_task.get(task.id, []))

        wave_results = run_wave(
            ready,
            executor_fn=_run_one,
            max_workers=max_parallel,
        )
        return WaveCycleResult(
            ready=ready,
            wave_results=wave_results,
            dependency_outputs_by_task=dependency_outputs_by_task,
        )

    def record_task_completion(
        self,
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
