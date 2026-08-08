"""Worker lease aggregate with atomic acquire/heartbeat/release."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized
from product_factory.workers.models import (
    WorkerLease,
    WorkerLeaseConflictError,
    WorkerLeaseLostError,
)


class WorkerRepository(AggregateRepository):
    def get_worker_lease(self, run_id: str) -> WorkerLease | None:
        row = self._conn.execute(
            "SELECT * FROM worker_leases WHERE run_id = ?", (run_id,)
        ).fetchone()
        return WorkerLease.model_validate(dict(row)) if row else None

    @synchronized
    def acquire_worker_lease(
        self,
        *,
        run_id: str,
        owner: str,
        worktree_key: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Atomically acquire or reclaim the exclusive lease for a run worktree."""
        from datetime import timedelta

        acquired_at = now or datetime.now(UTC)
        expires_at = acquired_at + timedelta(seconds=ttl_seconds)
        acquired_text = acquired_at.isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT * FROM worker_leases WHERE run_id = ?", (run_id,)
            ).fetchone()
            if (
                existing
                and existing["released_at"] is None
                and datetime.fromisoformat(existing["expires_at"]) > acquired_at
            ):
                raise WorkerLeaseConflictError(
                    f"Run {run_id} already has an active worker lease",
                    details={
                        "run_id": run_id,
                        "owner": existing["owner"],
                        "worktree_key": existing["worktree_key"],
                    },
                )

            # An expired lease cannot keep a worktree permanently locked. Its
            # owning run retains a typed recovery outcome for later inspection.
            self._conn.execute(
                """
                UPDATE worker_leases
                SET released_at=?, recovery_outcome=COALESCE(recovery_outcome, 'expired_reclaimed')
                WHERE worktree_key=? AND run_id<>? AND released_at IS NULL AND expires_at<=?
                """,
                (acquired_text, worktree_key, run_id, acquired_text),
            )
            conflict = self._conn.execute(
                """
                SELECT run_id, owner FROM worker_leases
                WHERE worktree_key=? AND run_id<>? AND released_at IS NULL AND expires_at>?
                """,
                (worktree_key, run_id, acquired_text),
            ).fetchone()
            if conflict:
                raise WorkerLeaseConflictError(
                    f"Worktree {worktree_key} already has an active writer",
                    details={
                        "run_id": conflict["run_id"],
                        "owner": conflict["owner"],
                        "worktree_key": worktree_key,
                    },
                )

            attempt = int(existing["attempt"]) + 1 if existing else 1
            self._conn.execute(
                """
                INSERT INTO worker_leases
                  (run_id, owner, attempt, heartbeat_at, expires_at, worktree_key,
                   recovery_outcome, released_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(run_id) DO UPDATE SET
                  owner=excluded.owner,
                  attempt=excluded.attempt,
                  heartbeat_at=excluded.heartbeat_at,
                  expires_at=excluded.expires_at,
                  worktree_key=excluded.worktree_key,
                  recovery_outcome=NULL,
                  released_at=NULL
                """,
                (
                    run_id,
                    owner,
                    attempt,
                    acquired_text,
                    expires_at.isoformat(),
                    worktree_key,
                ),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        lease = self.get_worker_lease(run_id)
        if lease is None:  # pragma: no cover - defensive against SQLite corruption
            raise WorkerLeaseLostError(f"Failed to read acquired lease for {run_id}")
        return lease

    def list_expired_worker_leases(self, *, now: datetime | None = None) -> list[WorkerLease]:
        expires_before = (now or datetime.now(UTC)).isoformat()
        rows = self._conn.execute(
            """
            SELECT * FROM worker_leases
            WHERE released_at IS NULL AND expires_at<=?
            ORDER BY expires_at, run_id
            """,
            (expires_before,),
        ).fetchall()
        return [WorkerLease.model_validate(dict(row)) for row in rows]

    @synchronized
    def release_worker_lease(
        self,
        *,
        run_id: str,
        owner: str,
        recovery_outcome: str,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Release a lease, retaining its final recovery outcome for audit."""
        released_at = now or datetime.now(UTC)
        cur = self._conn.execute(
            """
            UPDATE worker_leases SET released_at=?, recovery_outcome=?
            WHERE run_id=? AND owner=? AND released_at IS NULL
            """,
            (released_at.isoformat(), recovery_outcome, run_id, owner),
        )
        self._conn.commit()
        if cur.rowcount != 1:
            raise WorkerLeaseLostError(
                f"Cannot release worker lease for {run_id}; ownership changed",
                details={"run_id": run_id, "owner": owner},
            )
        lease = self.get_worker_lease(run_id)
        if lease is None:  # pragma: no cover - guarded by rowcount
            raise WorkerLeaseLostError(f"Worker lease for {run_id} disappeared")
        return lease

    def list_unleased_worker_runs(self) -> list[dict[str, Any]]:
        """Return nonterminal service runs with no active lease."""
        rows = self._conn.execute(
            """
            SELECT runs.* FROM runs
            LEFT JOIN worker_leases ON worker_leases.run_id = runs.run_id
              AND worker_leases.released_at IS NULL
            WHERE runs.status IN
              ('queued', 'initializing', 'planning', 'executing', 'validating', 'repairing')
              AND worker_leases.run_id IS NULL
            ORDER BY runs.created_at, runs.run_id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    @synchronized
    def heartbeat_worker_lease(
        self,
        *,
        run_id: str,
        owner: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> WorkerLease:
        """Extend a lease only while the caller still owns it."""
        from datetime import timedelta

        heartbeat_at = now or datetime.now(UTC)
        expires_at = heartbeat_at + timedelta(seconds=ttl_seconds)
        cur = self._conn.execute(
            """
            UPDATE worker_leases SET heartbeat_at=?, expires_at=?
            WHERE run_id=? AND owner=? AND released_at IS NULL
            """,
            (heartbeat_at.isoformat(), expires_at.isoformat(), run_id, owner),
        )
        self._conn.commit()
        if cur.rowcount != 1:
            raise WorkerLeaseLostError(
                f"Worker lease for {run_id} is no longer owned by {owner}",
                details={"run_id": run_id, "owner": owner},
            )
        lease = self.get_worker_lease(run_id)
        if lease is None:  # pragma: no cover - guarded by rowcount
            raise WorkerLeaseLostError(f"Worker lease for {run_id} disappeared")
        return lease
