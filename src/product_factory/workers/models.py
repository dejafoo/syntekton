"""Typed contracts for durable worker leases."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from product_factory.domain.errors import RuntimeFailureError


class WorkerLease(BaseModel):
    """A persisted exclusive writer lease for one run worktree."""

    run_id: str
    owner: str
    attempt: int
    heartbeat_at: datetime
    expires_at: datetime
    worktree_key: str
    recovery_outcome: str | None = None
    released_at: datetime | None = None


class WorkerLeaseConflictError(RuntimeFailureError):
    """Raised when an active worker already owns a run or worktree."""


class WorkerLeaseLostError(RuntimeFailureError):
    """Raised when a worker can no longer heartbeat its lease."""
