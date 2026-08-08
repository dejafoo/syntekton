"""Task and dependency aggregate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized


class TaskRepository(AggregateRepository):
    def list_tasks(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY task_id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


    def list_task_dependencies(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM task_dependencies WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


    def list_tasks_in_creation_order(self, run_id: str) -> list[dict[str, Any]]:
        """Tasks ordered by first-insert (rowid), a valid topological order.

        Used by durable resume (P1.B) to rebuild the live plan's task_order:
        a task is only ever inserted once its dependencies have already
        succeeded/skipped, so insertion order is always dependency-consistent.
        """
        rows = self._conn.execute(
            "SELECT rowid AS _rowid, * FROM tasks WHERE run_id = ? ORDER BY _rowid", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]


    @synchronized
    def get_task(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? AND task_id = ?", (run_id, task_id)
        ).fetchone()
        return dict(row) if row else None


    @synchronized
    def upsert_task(
        self,
        *,
        run_id: str,
        task_id: str,
        capability: str,
        status: str,
        spec: dict[str, Any],
        result: dict[str, Any] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        attempt: int | None = None,
        active_operation: str | None = None,
        effective_policy: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self._conn.execute(
            "SELECT attempt, started_at, effective_policy_json FROM tasks WHERE run_id=? AND task_id=?",
            (run_id, task_id),
        ).fetchone()
        policy_json = (
            json.dumps(effective_policy, default=str)
            if effective_policy is not None
            else (existing["effective_policy_json"] if existing else None)
        )
        if existing:
            next_attempt = attempt if attempt is not None else int(existing["attempt"] or 1)
            self._conn.execute(
                """
                UPDATE tasks SET capability=?, status=?, spec_json=?, result_json=?,
                  started_at=COALESCE(?, started_at),
                  ended_at=COALESCE(?, ended_at),
                  attempt=?,
                  updated_at=?,
                  active_operation=?,
                  effective_policy_json=COALESCE(?, effective_policy_json)
                WHERE run_id=? AND task_id=?
                """,
                (
                    capability,
                    status,
                    json.dumps(spec, default=str),
                    json.dumps(result, default=str) if result else None,
                    started_at,
                    ended_at,
                    next_attempt,
                    now,
                    active_operation,
                    policy_json,
                    run_id,
                    task_id,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO tasks
                (run_id, task_id, capability, status, spec_json, result_json,
                 started_at, ended_at, attempt, updated_at, active_operation,
                 effective_policy_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    capability,
                    status,
                    json.dumps(spec, default=str),
                    json.dumps(result, default=str) if result else None,
                    started_at or (now if status == "running" else None),
                    ended_at,
                    attempt or 1,
                    now,
                    active_operation,
                    policy_json,
                ),
            )
        for dep in spec.get("dependencies") or []:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO task_dependencies (run_id, task_id, depends_on)
                VALUES (?, ?, ?)
                """,
                (run_id, task_id, dep),
            )
        self._conn.commit()

