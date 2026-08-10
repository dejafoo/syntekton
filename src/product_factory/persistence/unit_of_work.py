"""Explicit unit of work over SqliteActor.immediate() (SR3.A).

Coupled run/task/event mutations commit or roll back together. Repository
methods participating in an open unit of work defer commit to this boundary.
SSE and other projections must publish only after the unit of work commits.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from product_factory.observability.contracts import ObservabilityEvent
    from product_factory.persistence.connection import SqliteActor
    from product_factory.persistence.database import Database
    from product_factory.persistence.repositories.events import EventRepository
    from product_factory.persistence.repositories.runs import RunRepository
    from product_factory.persistence.repositories.tasks import TaskRepository


class UnitOfWork:
    """Atomic boundary for coupled authoritative state and durable events."""

    def __init__(
        self,
        actor: SqliteActor,
        *,
        runs: RunRepository,
        tasks: TaskRepository,
        events: EventRepository,
    ) -> None:
        self._actor = actor
        self.runs = runs
        self.tasks = tasks
        self.events = events

    @classmethod
    def from_database(cls, db: Database) -> UnitOfWork:
        return cls(db._actor, runs=db.runs, tasks=db.tasks, events=db.events)

    @property
    def in_transaction(self) -> bool:
        return self._actor.in_transaction

    @contextmanager
    def begin(self) -> Iterator[UnitOfWork]:
        """Open an explicit multi-step transaction for ad-hoc coupled writes."""
        with self._actor.immediate():
            yield self

    @contextmanager
    def _atomic(self) -> Iterator[None]:
        """Join an open UoW transaction or start a dedicated immediate one."""
        if self._actor.in_transaction:
            yield
        else:
            with self._actor.immediate():
                yield

    def admit_run_with_events(
        self,
        *,
        run_id: str,
        workflow_type: str,
        status: str,
        request: dict[str, Any],
        events: list[ObservabilityEvent],
        base_commit: str | None = None,
        usage: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
        active_operation: str | None = None,
        budget_snapshot: dict[str, Any] | None = None,
    ) -> list[int]:
        """Persist run admission and initial events in one transaction."""
        seqs: list[int] = []
        with self._atomic():
            self.runs.upsert_run(
                run_id=run_id,
                workflow_type=workflow_type,
                status=status,
                request=request,
                base_commit=base_commit,
                usage=usage,
                manifest=manifest,
                active_operation=active_operation,
                budget_snapshot=budget_snapshot,
            )
            for event in events:
                seqs.append(self.events.append_event(event))
        return seqs

    def complete_task_with_event(
        self,
        *,
        run_id: str,
        task_id: str,
        capability: str,
        status: str,
        spec: dict[str, Any],
        event: ObservabilityEvent,
        result: dict[str, Any] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        attempt: int | None = None,
        active_operation: str | None = None,
        effective_policy: dict[str, Any] | None = None,
    ) -> int:
        """Persist task completion and its observability event together."""
        with self._atomic():
            self.tasks.upsert_task(
                run_id=run_id,
                task_id=task_id,
                capability=capability,
                status=status,
                spec=spec,
                result=result,
                started_at=started_at,
                ended_at=ended_at,
                attempt=attempt,
                active_operation=active_operation,
                effective_policy=effective_policy,
            )
            return self.events.append_event(event)

    def start_task_with_event(
        self,
        *,
        run_id: str,
        task_id: str,
        capability: str,
        spec: dict[str, Any],
        attempt_id: str,
        attempt_number: int,
        idempotency_key: str,
        reserved_cost_usd: str,
        event: ObservabilityEvent,
        started_at: str | None = None,
    ) -> int:
        """Atomically start a task attempt, reserve budget, and append its event."""

        now = started_at or datetime.now(UTC).isoformat()
        with self._atomic():
            self.tasks.upsert_task(
                run_id=run_id,
                task_id=task_id,
                capability=capability,
                status="running",
                spec=spec,
                started_at=now,
                attempt=attempt_number,
                active_operation=capability,
            )
            conn = self._actor.connection
            conn.execute(
                """
                INSERT INTO task_attempts
                (attempt_id, run_id, task_id, attempt_number, idempotency_key,
                 state, admitted_at, started_at)
                VALUES (?, ?, ?, ?, ?, 'started', ?, ?)
                """,
                (attempt_id, run_id, task_id, attempt_number, idempotency_key, now, now),
            )
            conn.execute(
                """
                INSERT INTO budget_reservations
                (reservation_id, run_id, task_id, attempt_id, reserved_cost_usd,
                 state, created_at)
                VALUES (?, ?, ?, ?, ?, 'reserved', ?)
                """,
                (f"budget-{attempt_id}", run_id, task_id, attempt_id, reserved_cost_usd, now),
            )
            return self.events.append_event(event)

    def settle_task_attempt_with_event(
        self,
        *,
        run_id: str,
        task_id: str,
        capability: str,
        spec: dict[str, Any],
        result: dict[str, Any],
        attempt_id: str,
        settled_cost_usd: str,
        tool_receipts: list[dict[str, Any]],
        event: ObservabilityEvent,
        ended_at: str | None = None,
    ) -> int:
        """Atomically settle one attempt and its authoritative terminal state."""

        now = ended_at or datetime.now(UTC).isoformat()
        status = str(result.get("status") or "failed")
        with self._atomic():
            self.tasks.upsert_task(
                run_id=run_id,
                task_id=task_id,
                capability=capability,
                status=status,
                spec=spec,
                result=result,
                ended_at=now,
                active_operation=None,
            )
            conn = self._actor.connection
            updated = conn.execute(
                """
                UPDATE task_attempts
                SET state='completed', completed_at=?, result_json=?, tool_receipts_json=?
                WHERE attempt_id=? AND run_id=? AND task_id=? AND state='started'
                """,
                (
                    now,
                    json.dumps(result, default=str),
                    json.dumps(tool_receipts),
                    attempt_id,
                    run_id,
                    task_id,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError(f"Attempt {attempt_id} is missing or already settled")
            settled = conn.execute(
                """
                UPDATE budget_reservations
                SET state='settled', settled_cost_usd=?, settled_at=?
                WHERE attempt_id=? AND state='reserved'
                """,
                (settled_cost_usd, now, attempt_id),
            )
            if settled.rowcount != 1:
                raise RuntimeError(f"Budget reservation for {attempt_id} is not reservable")
            return self.events.append_event(event)
