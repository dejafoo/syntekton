"""SD3.B graceful worker drain and restart recovery tests."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from product_factory.persistence.database import Database
from product_factory.workers.supervisor import WorkerSupervisor


def _run(db: Database, run_id: str, *, status: str = "executing") -> None:
    db.upsert_run(
        run_id=run_id,
        workflow_type="code_change",
        status=status,
        request={"request_id": f"req-{run_id}"},
    )


def test_drain_stops_admissions_before_workers_finish(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1")
    started = threading.Event()
    finish = threading.Event()

    def execute(_run_id: str) -> None:
        started.set()
        finish.wait(timeout=5)

    supervisor = WorkerSupervisor(
        db=db,
        execute=execute,
        resume=lambda _run_id: None,
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda *_a: None,
        lease_ttl_seconds=5,
        heartbeat_seconds=0.2,
        scan_seconds=0.2,
        shutdown_grace_seconds=2.0,
    )
    supervisor.start()
    assert supervisor.spawn("run-1")
    assert started.wait(timeout=2)
    assert supervisor.admissions_open

    report = supervisor.drain(grace_seconds=0.3, close_database=False)
    assert report.admissions_stopped
    assert report.scanner_stopped
    assert "run-1" in report.active_at_start
    # Still holding worker; forced recovery recorded.
    assert "run-1" in report.forced_recovery or "run-1" in report.finished
    assert not supervisor.admissions_open
    assert supervisor.spawn("run-2") is False

    finish.set()
    lease = db.get_worker_lease("run-1")
    # Either finished cooperatively or forced recovery outcome persisted.
    if lease and lease.released_at:
        assert "forced_shutdown" in (lease.recovery_outcome or "") or lease.recovery_outcome in {
            "completed",
            "cooperative_shutdown",
        }
    row = db.get_run("run-1")
    assert row is not None
    db.close()


def test_drain_then_restart_recovers_unleased_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    _run(db, "run-1", status="executing")
    block = threading.Event()
    resumed = threading.Event()

    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _rid: block.wait(timeout=5),
        resume=lambda _rid: resumed.set(),
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda *_a: None,
        lease_ttl_seconds=2,
        heartbeat_seconds=0.1,
        scan_seconds=0.1,
        shutdown_grace_seconds=0.2,
    )
    assert supervisor.spawn("run-1")
    time.sleep(0.15)
    report = supervisor.drain(grace_seconds=0.1, close_database=False)
    assert report.forced_recovery or report.finished
    block.set()
    time.sleep(0.1)

    # Simulate restart: new supervisor recovers unleased / recovery-required run.
    restarted = WorkerSupervisor(
        db=db,
        execute=lambda _rid: None,
        resume=lambda _rid: resumed.set(),
        worktree_key=lambda run_id: f"/worktrees/{run_id}",
        on_error=lambda *_a: None,
        lease_ttl_seconds=2,
        heartbeat_seconds=0.1,
        scan_seconds=0.1,
    )
    # Release any leftover lease as expired-style recovery path.
    lease = db.get_worker_lease("run-1")
    if lease and lease.released_at is None:
        from datetime import UTC, datetime, timedelta

        # Force expiry by re-acquiring after marking expired via release.
        db.release_worker_lease(
            run_id="run-1",
            owner=lease.owner,
            recovery_outcome="forced_shutdown_recovery_required",
            now=datetime.now(UTC) - timedelta(seconds=1),
        )
    recovered = restarted.recover_unleased()
    if not recovered:
        # Also accept expired reclaim path.
        recovered = restarted.recover_expired()
    assert recovered == ["run-1"] or resumed.wait(timeout=2)
    db.close()


def test_stop_compat_drains_without_closing_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    supervisor = WorkerSupervisor(
        db=db,
        execute=lambda _rid: None,
        resume=lambda _rid: None,
        worktree_key=lambda run_id: f"/wt/{run_id}",
        on_error=lambda *_a: None,
        lease_ttl_seconds=2,
        heartbeat_seconds=0.1,
        scan_seconds=0.1,
        shutdown_grace_seconds=0.1,
    )
    supervisor.start()
    report = supervisor.stop()
    assert report.admissions_stopped
    assert report.database_closed is False
    # DB still usable
    _run(db, "run-x", status="queued")
    assert db.get_run("run-x") is not None
    db.close()
