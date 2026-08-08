"""Run, invocation, tool-call, validator, and catalog aggregate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized


class RunRepository(AggregateRepository):
    def is_cancel_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        if not row:
            return False
        return bool(int(row.get("cancel_requested") or 0))

    @synchronized
    def record_tool_call(
        self,
        *,
        run_id: str,
        record: dict[str, Any],
        status: str = "completed",
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO tool_calls
            (tool_call_id, run_id, task_id, tool_name, record_json, status,
             started_at, ended_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["tool_call_id"],
                run_id,
                record["task_id"],
                record["tool_name"],
                json.dumps(record, default=str),
                status,
                started_at,
                ended_at or now,
                now,
            ),
        )
        self._conn.commit()

    def get_model_catalog(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_json, refreshed_at FROM model_catalog_cache WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        return {"payload": json.loads(row["payload_json"]), "refreshed_at": row["refreshed_at"]}

    def get_invocation(self, request_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM model_invocations WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    @synchronized
    def record_validator_results(
        self, *, run_id: str, task_id: str | None, results: list[dict[str, Any]]
    ) -> None:
        for result in results:
            self._conn.execute(
                """
                INSERT INTO validator_results (run_id, task_id, result_json)
                VALUES (?, ?, ?)
                """,
                (run_id, task_id, json.dumps(result, default=str)),
            )
        self._conn.commit()

    def list_invocations(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM model_invocations WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_validator_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM validator_results WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_runs(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @synchronized
    def set_cancel_requested(self, run_id: str, *, requested: bool = True) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            UPDATE runs SET cancel_requested=?, updated_at=?
            WHERE run_id=?
            """,
            (1 if requested else 0, now, run_id),
        )
        self._conn.commit()

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def cache_model_catalog(self, payload: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO model_catalog_cache (id, payload_json, refreshed_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              refreshed_at=excluded.refreshed_at
            """,
            (json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def record_invocation(
        self,
        *,
        request_id: str,
        run_id: str,
        task_id: str,
        model_profile: str,
        status: str,
        usage: dict[str, Any],
        response_hash: str | None,
        provider: str | None = None,
        resolved_model_id: str | None = None,
        prompt_package_hash: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        latency_ms: int | None = None,
        content_refs: list[dict[str, Any]] | None = None,
        routing: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO model_invocations
            (request_id, run_id, task_id, model_profile, status,
             usage_json, response_hash, provider, resolved_model_id,
             prompt_package_hash, started_at, ended_at, latency_ms,
             content_refs_json, routing_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                run_id,
                task_id,
                model_profile,
                status,
                json.dumps(usage, default=str),
                response_hash,
                provider,
                resolved_model_id,
                prompt_package_hash,
                started_at,
                ended_at or now,
                latency_ms,
                json.dumps(content_refs or []),
                json.dumps(routing or {}, default=str),
                now,
            ),
        )
        self._conn.commit()

    @synchronized
    def upsert_run(
        self,
        *,
        run_id: str,
        workflow_type: str,
        status: str,
        request: dict[str, Any],
        base_commit: str | None = None,
        usage: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
        active_operation: str | None = None,
        touch_progress: bool = True,
        budget_snapshot: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self._conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        progress = now if touch_progress else None
        budget_json = json.dumps(budget_snapshot, default=str) if budget_snapshot else None
        if existing:
            self._conn.execute(
                """
                UPDATE runs SET status=?, updated_at=?, base_commit=?, usage_json=?,
                  manifest_json=?,
                  last_progress_at=COALESCE(?, last_progress_at),
                  active_operation=COALESCE(?, active_operation),
                  budget_json=COALESCE(?, budget_json)
                WHERE run_id=?
                """,
                (
                    status,
                    now,
                    base_commit,
                    json.dumps(usage or {}),
                    json.dumps(manifest) if manifest else None,
                    progress,
                    active_operation,
                    budget_json,
                    run_id,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO runs
                (run_id, workflow_type, status, request_json, created_at, updated_at,
                 base_commit, usage_json, manifest_json, last_progress_at, active_operation,
                 budget_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workflow_type,
                    status,
                    json.dumps(request, default=str),
                    now,
                    now,
                    base_commit,
                    json.dumps(usage or {}),
                    json.dumps(manifest) if manifest else None,
                    now,
                    active_operation,
                    budget_json,
                ),
            )
        self._conn.commit()
