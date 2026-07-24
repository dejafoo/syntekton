"""Unit tests for wave partitioning and execution (P1.F)."""

from __future__ import annotations

import threading
import time

from product_factory.domain.tasks import TaskSpec
from product_factory.orchestration.concurrency import (
    detect_static_writer_conflict,
    partition_wave,
    run_wave,
)


def _task(task_id: str, capability: str, write_patterns: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        title=task_id,
        capability=capability,  # type: ignore[arg-type]
        objective="obj",
        expected_output_schema="schema.v1",
        writable_path_patterns=write_patterns or [],
    )


def test_read_only_tasks_are_always_concurrent() -> None:
    tasks = [
        _task("A", "repository_analysis"),
        _task("B", "independent_review"),
        _task("C", "security_review"),
    ]
    concurrent, serial = partition_wave(tasks)
    assert {t.id for t in concurrent} == {"A", "B", "C"}
    assert serial == []


def test_disjoint_writers_run_concurrently() -> None:
    tasks = [
        _task("A", "implementation", ["src/a/**"]),
        _task("B", "implementation", ["src/b/**"]),
    ]
    concurrent, serial = partition_wave(tasks)
    assert {t.id for t in concurrent} == {"A", "B"}
    assert serial == []


def test_conflicting_writers_are_serialized() -> None:
    tasks = [
        _task("A", "implementation", ["src/shared/**"]),
        _task("B", "implementation", ["src/shared/**"]),
    ]
    concurrent, serial = partition_wave(tasks)
    assert [t.id for t in concurrent] == ["A"]
    assert [t.id for t in serial] == ["B"]


def test_broad_write_pattern_conflicts_with_everything() -> None:
    tasks = [
        _task("A", "implementation", ["**/*"]),
        _task("B", "implementation", ["src/isolated/**"]),
    ]
    concurrent, serial = partition_wave(tasks)
    assert [t.id for t in concurrent] == ["A"]
    assert [t.id for t in serial] == ["B"]


def test_detect_static_writer_conflict_disjoint() -> None:
    a = _task("A", "implementation", ["src/a/**"])
    b = _task("B", "implementation", ["src/b/**"])
    assert not detect_static_writer_conflict(a, b)


def test_detect_static_writer_conflict_overlap() -> None:
    a = _task("A", "implementation", ["src/shared/**"])
    b = _task("B", "implementation", ["src/shared/**"])
    assert detect_static_writer_conflict(a, b)


def test_run_wave_returns_results_in_task_order_regardless_of_completion_order() -> None:
    tasks = [
        _task("A", "repository_analysis"),
        _task("B", "independent_review"),
        _task("C", "security_review"),
    ]
    delays = {"A": 0.05, "B": 0.0, "C": 0.02}

    def executor(task: TaskSpec) -> str:
        time.sleep(delays[task.id])
        return task.id

    results = run_wave(tasks, executor_fn=executor, max_workers=3)
    assert results == ["A", "B", "C"]


def test_run_wave_read_only_tasks_overlap_in_wall_clock() -> None:
    tasks = [
        _task("A", "repository_analysis"),
        _task("B", "independent_review"),
    ]
    intervals: dict[str, tuple[float, float]] = {}
    lock = threading.Lock()

    def executor(task: TaskSpec) -> str:
        start = time.monotonic()
        time.sleep(0.1)
        end = time.monotonic()
        with lock:
            intervals[task.id] = (start, end)
        return task.id

    run_wave(tasks, executor_fn=executor, max_workers=2)
    (a_start, a_end) = intervals["A"]
    (b_start, b_end) = intervals["B"]
    # Overlapping wall-clock intervals: each task starts before the other ends.
    assert a_start < b_end
    assert b_start < a_end


def test_run_wave_serializes_conflicting_writers() -> None:
    tasks = [
        _task("A", "implementation", ["src/shared/**"]),
        _task("B", "implementation", ["src/shared/**"]),
    ]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def executor(task: TaskSpec) -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return task.id

    results = run_wave(tasks, executor_fn=executor, max_workers=4)
    assert results == ["A", "B"]
    assert max_active == 1


def test_run_wave_empty_tasks() -> None:
    assert run_wave([], executor_fn=lambda t: t.id, max_workers=3) == []


def test_run_wave_single_worker_bound() -> None:
    tasks = [_task("A", "repository_analysis"), _task("B", "independent_review")]
    active = 0
    max_active = 0
    lock = threading.Lock()

    def executor(task: TaskSpec) -> str:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return task.id

    run_wave(tasks, executor_fn=executor, max_workers=1)
    assert max_active == 1
