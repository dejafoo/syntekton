"""Observability event append/query aggregate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized

if TYPE_CHECKING:
    from product_factory.observability.contracts import ObservabilityEvent


class EventRepository(AggregateRepository):
    def list_events(
        self,
        *,
        run_id: str | None = None,
        after_seq: int = 0,
        limit: int = 200,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["seq > ?"]
        params: list[Any] = [after_seq]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(types)
        params.append(limit)
        sql = f"""
            SELECT * FROM events
            WHERE {" AND ".join(clauses)}
            ORDER BY seq ASC
            LIMIT ?
        """
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @synchronized
    def append_event(self, event: ObservabilityEvent) -> int:
        now = datetime.now(UTC).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO events
            (event_id, occurred_at, recorded_at, event_type, schema_version,
             run_id, task_id, request_id, tool_call_id, trace_id, span_id,
             parent_span_id, severity, summary, payload_json, content_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.occurred_at.isoformat(),
                now,
                event.type,
                event.schema_version,
                event.run_id,
                event.task_id,
                event.request_id,
                event.tool_call_id,
                event.trace_id,
                event.span_id,
                event.parent_span_id,
                event.severity.value if hasattr(event.severity, "value") else str(event.severity),
                event.summary,
                json.dumps(event.payload, default=str),
                json.dumps([c.model_dump(mode="json") for c in event.content_refs]),
            ),
        )
        self._conn.execute(
            """
            UPDATE runs SET last_progress_at=?, updated_at=?
            WHERE run_id=?
            """,
            (now, now, event.run_id),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def count_error_events(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE run_id = ? AND severity = 'error'",
            (run_id,),
        ).fetchone()
        return int(row["c"] if row else 0)

    def latest_seq(self) -> int:
        row = self._conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()
        return int(row["m"] if row else 0)

    def latest_seq_for_run(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["m"] if row else 0)

    def last_event_at(self) -> str | None:
        row = self._conn.execute(
            "SELECT recorded_at FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["recorded_at"] if row else None
