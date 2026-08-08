"""Durable worker supervision with heartbeat, restart recovery, and graceful drain."""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from product_factory.persistence.database import Database
from product_factory.workers.models import WorkerLeaseConflictError, WorkerLeaseLostError

_TERMINAL_RUN_STATUSES = frozenset(
    {
        "awaiting_approval",
        "blocked",
        "budget_exhausted",
        "cancelled",
        "completed",
        "failed",
        "plan_rejected",
    }
)


@dataclass(slots=True)
class ShutdownReport:
    """Outcome of a graceful supervisor drain (SD3.B)."""

    admissions_stopped: bool = True
    scanner_stopped: bool = False
    waited_seconds: float = 0.0
    active_at_start: list[str] = field(default_factory=list)
    finished: list[str] = field(default_factory=list)
    forced_recovery: list[str] = field(default_factory=list)
    database_closed: bool = False


class WorkerSupervisor:
    """Run workers under exclusive leases and reclaim expired work after restart."""

    def __init__(
        self,
        *,
        db: Database,
        execute: Callable[[str], Any],
        resume: Callable[[str], Any],
        worktree_key: Callable[[str], str],
        on_error: Callable[[str, Exception, bool], None],
        lease_ttl_seconds: float = 30.0,
        heartbeat_seconds: float = 10.0,
        scan_seconds: float = 5.0,
        shutdown_grace_seconds: float = 15.0,
    ) -> None:
        if heartbeat_seconds >= lease_ttl_seconds:
            raise ValueError("heartbeat_seconds must be shorter than lease_ttl_seconds")
        self.db = db
        self.execute = execute
        self.resume = resume
        self.worktree_key = worktree_key
        self.on_error = on_error
        self.lease_ttl_seconds = lease_ttl_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.scan_seconds = scan_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.instance_id = f"host-{uuid.uuid4().hex[:12]}"
        self._stop = threading.Event()
        self._admissions_open = threading.Event()
        self._admissions_open.set()
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self._scanner: threading.Thread | None = None
        self._cooperative_shutdown = threading.Event()

    @property
    def running(self) -> bool:
        return bool(self._scanner and self._scanner.is_alive())

    @property
    def admissions_open(self) -> bool:
        return self._admissions_open.is_set()

    def start(self) -> None:
        """Start the expiry scanner once."""
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._cooperative_shutdown.clear()
            self._admissions_open.set()
            self._scanner = threading.Thread(
                target=self._scan_loop,
                name="pf-worker-lease-scanner",
                daemon=True,
            )
            self._scanner.start()

    def stop(self, *, grace_seconds: float | None = None) -> ShutdownReport:
        """Graceful drain: stop admissions → wait → forced recovery → leave DB open.

        Database closing is owned by the caller (HostService) and must happen
        only after this method returns.
        """
        return self.drain(grace_seconds=grace_seconds, close_database=False)

    def drain(
        self,
        *,
        grace_seconds: float | None = None,
        close_database: bool = False,
    ) -> ShutdownReport:
        """Stop admissions and recovery scanning, then drain active workers.

        Order (SD3.B):
        1. Stop new admissions and recovery scanning.
        2. Signal cooperative shutdown to active workers.
        3. Wait up to ``grace_seconds`` for workers to finish.
        4. Persist forced-shutdown / recovery-required outcomes for stragglers.
        5. Optionally close the database last (normally HostService owns close).
        """
        grace = self.shutdown_grace_seconds if grace_seconds is None else max(0.0, grace_seconds)
        report = ShutdownReport(admissions_stopped=True)
        self._admissions_open.clear()
        self._cooperative_shutdown.set()
        self._stop.set()
        scanner = self._scanner
        if scanner and scanner.is_alive():
            scanner.join(timeout=max(1.0, self.scan_seconds + 0.1))
        report.scanner_stopped = not (scanner and scanner.is_alive())

        with self._lock:
            active = {
                run_id: worker
                for run_id, worker in self._workers.items()
                if worker.is_alive()
            }
        report.active_at_start = sorted(active)

        # Cooperative wait loop.
        import time

        started = time.monotonic()
        remaining = dict(active)
        while remaining and (time.monotonic() - started) < grace:
            for run_id, worker in list(remaining.items()):
                worker.join(timeout=0.05)
                if not worker.is_alive():
                    report.finished.append(run_id)
                    remaining.pop(run_id, None)
            if remaining:
                time.sleep(0.05)
        report.waited_seconds = time.monotonic() - started

        # Forced recovery for workers that did not finish.
        for run_id, worker in remaining.items():
            self._record_forced_shutdown(run_id)
            report.forced_recovery.append(run_id)
            # Do not join forever; durable recovery owns continuation after restart.
            worker.join(timeout=0.1)

        if close_database:
            self.db.close()
            report.database_closed = True
        return report

    def spawn(self, run_id: str, *, recovery: bool = False, recovered_queue: bool = False) -> bool:
        """Start one supervised worker unless this process already has one."""
        if not self._admissions_open.is_set() and not recovery:
            return False
        # During drain, refuse both new admissions and recovery scanning spawns.
        if self._stop.is_set() and not self._cooperative_shutdown.is_set():
            return False
        if self._stop.is_set():
            # Scanner is stopped; only an explicit in-flight path should reach here.
            return False
        with self._lock:
            current = self._workers.get(run_id)
            if current and current.is_alive():
                return False
            worker = threading.Thread(
                target=self._run,
                kwargs={
                    "run_id": run_id,
                    "recovery": recovery,
                    "recovered_queue": recovered_queue,
                },
                name=f"pf-supervised-worker-{run_id}",
                daemon=True,
            )
            self._workers[run_id] = worker
            worker.start()
            return True

    def wait(self, run_id: str, *, timeout: float | None = None) -> bool:
        """Wait for this process's worker, returning false on timeout."""
        with self._lock:
            worker = self._workers.get(run_id)
        if worker is None:
            return True
        worker.join(timeout=timeout)
        return not worker.is_alive()

    def run_blocking(self, run_id: str, *, recovery: bool = False) -> Any:
        """Execute under a lease in the current thread (primarily for tests)."""
        return self._run_leased(run_id, recovery=recovery, recovered_queue=False)

    def recover_expired(self) -> list[str]:
        """Dispatch every currently expired lease through durable resume."""
        if not self._admissions_open.is_set() or self._stop.is_set():
            return []
        recovered: list[str] = []
        for lease in self.db.list_expired_worker_leases():
            row = self.db.get_run(lease.run_id)
            if row is None or row["status"] in _TERMINAL_RUN_STATUSES:
                with contextlib.suppress(WorkerLeaseLostError):
                    self.db.release_worker_lease(
                        run_id=lease.run_id,
                        owner=lease.owner,
                        recovery_outcome="terminal_observed",
                    )
                continue
            if self.spawn(lease.run_id, recovery=True):
                recovered.append(lease.run_id)
        return recovered

    def recover_unleased(self) -> list[str]:
        """Dispatch queued or interrupted runs left without a lease."""
        if not self._admissions_open.is_set() or self._stop.is_set():
            return []
        recovered: list[str] = []
        for row in self.db.list_unleased_worker_runs():
            queued = row["status"] == "queued"
            if self.spawn(
                row["run_id"],
                recovery=not queued,
                recovered_queue=queued,
            ):
                recovered.append(row["run_id"])
        return recovered

    def _record_forced_shutdown(self, run_id: str) -> None:
        """Persist recovery-required state without closing the DB connection."""
        lease = self.db.get_worker_lease(run_id)
        if lease is None or lease.released_at is not None:
            # Still mark run active_operation for restart scanners.
            row = self.db.get_run(run_id)
            if row and row["status"] not in _TERMINAL_RUN_STATUSES:
                self.db.upsert_run(
                    run_id=run_id,
                    workflow_type=row["workflow_type"],
                    status=row["status"],
                    request=json.loads(row["request_json"])
                    if isinstance(row.get("request_json"), str)
                    else (row.get("request") or {}),
                    active_operation="forced_shutdown_recovery_required",
                    touch_progress=False,
                )
            return
        with contextlib.suppress(WorkerLeaseLostError):
            self.db.release_worker_lease(
                run_id=run_id,
                owner=lease.owner,
                recovery_outcome="forced_shutdown_recovery_required",
            )
        row = self.db.get_run(run_id)
        if row and row["status"] not in _TERMINAL_RUN_STATUSES:
            request = row.get("request_json")
            if isinstance(request, str):
                request = json.loads(request)
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=row["workflow_type"],
                status=row["status"],
                request=request or {},
                active_operation="forced_shutdown_recovery_required",
                touch_progress=False,
            )

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            if self._admissions_open.is_set():
                self.recover_expired()
                self.recover_unleased()
            self._stop.wait(self.scan_seconds)

    def _run(self, *, run_id: str, recovery: bool, recovered_queue: bool) -> None:
        try:
            self._run_leased(
                run_id,
                recovery=recovery,
                recovered_queue=recovered_queue,
            )
        except WorkerLeaseConflictError:
            # Another process won the durable lease race. It owns the run; this
            # duplicate dispatcher must not rewrite the run as failed.
            return
        except Exception as exc:  # noqa: BLE001 - callback persists typed failure
            self.on_error(run_id, exc, recovery)
        finally:
            with self._lock:
                current = self._workers.get(run_id)
                if current is threading.current_thread():
                    self._workers.pop(run_id, None)

    def _run_leased(self, run_id: str, *, recovery: bool, recovered_queue: bool) -> Any:
        owner = f"{self.instance_id}:{run_id}:{uuid.uuid4().hex[:8]}"
        self.db.acquire_worker_lease(
            run_id=run_id,
            owner=owner,
            worktree_key=self.worktree_key(run_id),
            ttl_seconds=self.lease_ttl_seconds,
        )
        heartbeat_stop = threading.Event()
        heartbeat_error: list[Exception] = []

        def heartbeat() -> None:
            while not heartbeat_stop.wait(self.heartbeat_seconds):
                if self._cooperative_shutdown.is_set():
                    return
                try:
                    self.db.heartbeat_worker_lease(
                        run_id=run_id,
                        owner=owner,
                        ttl_seconds=self.lease_ttl_seconds,
                    )
                except Exception as exc:  # noqa: BLE001 - surfaced after target returns
                    heartbeat_error.append(exc)
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"pf-worker-heartbeat-{run_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        outcome = (
            "resumed" if recovery else ("recovered_queued" if recovered_queue else "completed")
        )
        try:
            if self._cooperative_shutdown.is_set():
                outcome = "cooperative_shutdown"
                return None
            result = self.resume(run_id) if recovery else self.execute(run_id)
            final_status = getattr(result, "final_status", None)
            if recovery and final_status == "failed":
                outcome = "recoverable_failure:failed"
            if heartbeat_error:
                raise WorkerLeaseLostError(
                    f"Heartbeat failed while worker {run_id} was running",
                    details={"error": str(heartbeat_error[0])},
                )
            return result
        except Exception as exc:
            outcome = (
                f"recoverable_failure:{exc.__class__.__name__}"
                if recovery
                else f"worker_failure:{exc.__class__.__name__}"
            )
            raise
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=max(1.0, self.heartbeat_seconds + 0.1))
            # A replacement owner may already have reclaimed an expired lease;
            # never release or mutate that replacement's lease.
            with contextlib.suppress(WorkerLeaseLostError):
                self.db.release_worker_lease(
                    run_id=run_id,
                    owner=owner,
                    recovery_outcome=outcome,
                )
