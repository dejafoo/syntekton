"""Durable worker supervision with heartbeat and restart recovery."""

from __future__ import annotations

import contextlib
import threading
import uuid
from collections.abc import Callable
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
        self.instance_id = f"host-{uuid.uuid4().hex[:12]}"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers: dict[str, threading.Thread] = {}
        self._scanner: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._scanner and self._scanner.is_alive())

    def start(self) -> None:
        """Start the expiry scanner once."""
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._scanner = threading.Thread(
                target=self._scan_loop,
                name="pf-worker-lease-scanner",
                daemon=True,
            )
            self._scanner.start()

    def stop(self) -> None:
        """Stop scanning; active daemon workers retain leases until process exit."""
        self._stop.set()
        scanner = self._scanner
        if scanner and scanner.is_alive():
            scanner.join(timeout=max(1.0, self.scan_seconds + 0.1))

    def spawn(self, run_id: str, *, recovery: bool = False, recovered_queue: bool = False) -> bool:
        """Start one supervised worker unless this process already has one."""
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

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
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
