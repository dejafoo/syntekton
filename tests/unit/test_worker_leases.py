"""Durable worker lease and supervisor recovery tests (PM4.D2)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from product_factory.persistence.database import Database
from product_factory.workers.models import WorkerLeaseConflictError
from product_factory.workers.supervisor import WorkerSupervisor


def _run(db: Database, run_id: str) -> None:
    db.upsert_run(
        run_id=run_id,
        workflow_type="code_change",
        status="executing",
        request={"request_id": f"req-{run_id}"},
    )


def test_one_active_writer_per_worktree(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    _run(db, "run-2")

    lease = db.acquire_worker_lease(
        run_id="run-1",
        owner="host-a",
        worktree_key="/worktrees/shared",
        ttl_seconds=30,
    )
    assert lease.attempt == 1

    with pytest.raises(WorkerLeaseConflictError, match="active writer"):
        db.acquire_worker_lease(
            run_id="run-2",
            owner="host-b",
            worktree_key="/worktrees/shared",
            ttl_seconds=30,
        )


def test_independent_worktree_runs_may_proceed_concurrently(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    _run(db, "run-2")

    first = db.acquire_worker_lease(
        run_id="run-1",
        owner="host-a",
        worktree_key="/worktrees/run-1",
        ttl_seconds=30,
    )
    second = db.acquire_worker_lease(
        run_id="run-2",
        owner="host-b",
        worktree_key="/worktrees/run-2",
        ttl_seconds=30,
    )

    assert first.released_at is None
    assert second.released_at is None
    assert first.worktree_key != second.worktree_key


def test_expired_lease_is_reclaimed_with_incremented_attempt(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    started = datetime.now(UTC) - timedelta(minutes=1)
    db.acquire_worker_lease(
        run_id="run-1",
        owner="dead-host",
        worktree_key="/worktrees/run-1",
        ttl_seconds=1,
        now=started,
    )

    recovered = db.acquire_worker_lease(
        run_id="run-1",
        owner="replacement-host",
        worktree_key="/worktrees/run-1",
        ttl_seconds=30,
    )
    assert recovered.owner == "replacement-host"
    assert recovered.attempt == 2
    assert recovered.recovery_outcome is None


def test_supervisor_heartbeats_then_releases(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    finish = threading.Event()

    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _run_id: finish.wait(timeout=2),
        resume=lambda _run_id: None,
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda _run_id, exc, _recovery: pytest.fail(str(exc)),
        lease_ttl_seconds=0.3,
        heartbeat_seconds=0.05,
        scan_seconds=0.05,
    )
    assert supervisor.spawn("run-1")

    deadline = time.time() + 2
    first = None
    while time.time() < deadline:
        first = db.get_worker_lease("run-1")
        if first is not None:
            break
        time.sleep(0.01)
    assert first is not None
    time.sleep(0.12)
    heartbeat = db.get_worker_lease("run-1")
    assert heartbeat is not None
    assert heartbeat.heartbeat_at > first.heartbeat_at

    finish.set()
    deadline = time.time() + 2
    while time.time() < deadline:
        released = db.get_worker_lease("run-1")
        if released and released.released_at is not None:
            break
        time.sleep(0.01)
    assert released is not None
    assert released.recovery_outcome == "completed"


def test_expiry_scan_resumes_and_records_outcome(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    db.acquire_worker_lease(
        run_id="run-1",
        owner="dead-host",
        worktree_key="/worktrees/run-1",
        ttl_seconds=1,
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    resumed = threading.Event()
    errors: list[Exception] = []
    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _run_id: None,
        resume=lambda _run_id: resumed.set(),
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda _run_id, exc, _recovery: errors.append(exc),
        lease_ttl_seconds=1,
        heartbeat_seconds=0.1,
        scan_seconds=0.05,
    )

    assert supervisor.recover_expired() == ["run-1"]
    assert resumed.wait(timeout=2)
    deadline = time.time() + 2
    while time.time() < deadline:
        lease = db.get_worker_lease("run-1")
        if lease and lease.released_at is not None:
            break
        time.sleep(0.01)
    assert errors == []
    assert lease is not None
    assert lease.attempt == 2
    assert lease.recovery_outcome == "resumed"


def test_expired_terminal_run_is_released_without_resume(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    db.acquire_worker_lease(
        run_id="run-1",
        owner="dead-host",
        worktree_key="/worktrees/run-1",
        ttl_seconds=1,
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    row = db.get_run("run-1")
    assert row is not None
    db.upsert_run(
        run_id="run-1",
        workflow_type=row["workflow_type"],
        status="completed",
        request={"request_id": "req-run-1"},
    )
    resumed: list[str] = []
    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _run_id: None,
        resume=lambda run_id: resumed.append(run_id),
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda _run_id, exc, _recovery: pytest.fail(str(exc)),
        lease_ttl_seconds=1,
        heartbeat_seconds=0.1,
    )

    assert supervisor.recover_expired() == []
    assert resumed == []
    lease = db.get_worker_lease("run-1")
    assert lease is not None
    assert lease.released_at is not None
    assert lease.recovery_outcome == "terminal_observed"


def test_recovery_failure_is_typed_and_retained(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    db.acquire_worker_lease(
        run_id="run-1",
        owner="dead-host",
        worktree_key="/worktrees/run-1",
        ttl_seconds=1,
        now=datetime.now(UTC) - timedelta(minutes=1),
    )
    observed: list[tuple[Exception, bool]] = []

    def fail_resume(_run_id: str) -> None:
        raise RuntimeError("resume failed")

    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _run_id: None,
        resume=fail_resume,
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda _run_id, exc, recovery: observed.append((exc, recovery)),
        lease_ttl_seconds=1,
        heartbeat_seconds=0.1,
    )
    assert supervisor.recover_expired() == ["run-1"]

    deadline = time.time() + 2
    while time.time() < deadline and not observed:
        time.sleep(0.01)
    assert len(observed) == 1
    assert observed[0][1] is True
    lease = db.get_worker_lease("run-1")
    assert lease is not None
    assert lease.recovery_outcome == "recoverable_failure:RuntimeError"
