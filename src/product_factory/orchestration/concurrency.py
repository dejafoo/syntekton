"""Wave-level concurrency: classify ready tasks as safely parallel or serial (P1.F).

Two classes of ready tasks may run concurrently within a wave:

- Always-eligible read-only capabilities (they never mutate the worktree they
  were assigned, so isolated worktrees make them independent by construction).
- Writers, but only when their statically-declared write path patterns are
  predicted disjoint via `detect_static_writer_conflict`. Any predicted
  overlap forces serialization of the conflicting pair (fail-safe, not
  fail-fast: we avoid the race instead of racing and detecting after).

Execution order within the concurrent group is not guaranteed, but result
merge order is always deterministic (plan/task_order), which is what the
rest of the coordinator relies on.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from product_factory.domain.tasks import TaskSpec

ALWAYS_CONCURRENT_CAPABILITIES: frozenset[str] = frozenset(
    {
        "repository_analysis",
        "independent_review",
        "security_review",
        "requirements",
        "test_execution",
        "domain_research",
        "decision_analysis",
    }
)

_BROAD_PATTERNS: frozenset[str] = frozenset({"**/*", "**", "*"})


def _patterns_overlap(a: list[str], b: list[str]) -> bool:
    """Conservative static overlap prediction over declared write patterns."""
    a = a or ["**/*"]
    b = b or ["**/*"]
    if any(p in _BROAD_PATTERNS for p in a) or any(p in _BROAD_PATTERNS for p in b):
        return True
    return bool(set(a) & set(b))


def detect_static_writer_conflict(task_a: TaskSpec, task_b: TaskSpec) -> bool:
    """Predict whether two writer tasks might touch overlapping paths."""
    return _patterns_overlap(task_a.effective_write_patterns(), task_b.effective_write_patterns())


def partition_wave(tasks: list[TaskSpec]) -> tuple[list[TaskSpec], list[TaskSpec]]:
    """Split a ready wave into (concurrent_group, serial_group).

    `concurrent_group` tasks may run in a bounded thread pool simultaneously.
    `serial_group` tasks must run one at a time, in the given order, after
    (or interleaved with, in practice: after) the concurrent group.
    """
    concurrent: list[TaskSpec] = []
    writers: list[TaskSpec] = []
    for task in tasks:
        if task.capability in ALWAYS_CONCURRENT_CAPABILITIES:
            concurrent.append(task)
        else:
            writers.append(task)

    serial: list[TaskSpec] = []
    parallel_writers: list[TaskSpec] = []
    for task in writers:
        conflicts = any(
            detect_static_writer_conflict(task, other) for other in (*parallel_writers, *serial)
        )
        if conflicts:
            serial.append(task)
        else:
            parallel_writers.append(task)
    concurrent.extend(parallel_writers)
    return concurrent, serial


def run_wave[T](
    tasks: list[TaskSpec],
    *,
    executor_fn: Callable[[TaskSpec], T],
    max_workers: int,
) -> list[T]:
    """Run a ready wave, deterministically returning results in `tasks` order."""
    if not tasks:
        return []
    concurrent_tasks, serial_tasks = partition_wave(tasks)
    results: dict[str, T] = {}
    if concurrent_tasks:
        workers = max(1, min(max_workers, len(concurrent_tasks)))
        if workers == 1:
            for task in concurrent_tasks:
                results[task.id] = executor_fn(task)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(executor_fn, task): task.id for task in concurrent_tasks}
                for future, task_id in futures.items():
                    results[task_id] = future.result()
    for task in serial_tasks:
        results[task.id] = executor_fn(task)
    order = {task.id: i for i, task in enumerate(tasks)}
    return [results[task_id] for task_id in sorted(results, key=lambda tid: order[tid])]
