"""WaveExecutionService — runnable selection and wave-cycle ownership (SR2).

Ownership boundary
------------------
* ``WaveScheduler`` remains decision-only: ready-task selection, concurrency
  slot accounting, and transitive dependency walks.
* ``WaveExecutionService`` owns the wave-cycle orchestration surface that the
  lifecycle engine sequences. The first extraction cut exposes ``select_ready``
  (and thin scheduler delegates). Full ``run_wave_cycle`` (dispatch, result
  collection, validation/repair hooks, heartbeat) lands in a later SR2 pass.

Do not import the compatibility facade or grow scheduling decisions here.
"""

from __future__ import annotations

from product_factory.domain.plans import CompiledPlan
from product_factory.domain.tasks import TaskSpec
from product_factory.scheduling.scheduler import WaveScheduler


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
