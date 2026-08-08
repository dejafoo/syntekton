"""Durable handoff record and consumption aggregate."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized


class HandoffRepository(AggregateRepository):
    @synchronized
    def insert_handoff_consumption(self, consumption: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO handoff_consumptions (
                consumer_run_id, handoff_id, producer_artifact_instance_id,
                consumer_artifact_instance_id, state_at_resolution, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                consumption["consumer_run_id"],
                consumption["handoff_id"],
                consumption["producer_artifact_instance_id"],
                consumption["consumer_artifact_instance_id"],
                consumption["state_at_resolution"],
                consumption["resolved_at"],
            ),
        )
        self._conn.commit()


    @synchronized
    def list_handoff_consumptions(self, handoff_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM handoff_consumptions WHERE handoff_id = ? ORDER BY resolved_at",
            (handoff_id,),
        ).fetchall()
        return [dict(row) for row in rows]


    @synchronized
    def list_handoff_records_by_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM handoff_records WHERE producer_run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [decoded for row in rows if (decoded := self._decode_handoff_record(row))]


    @synchronized
    def get_handoff_record(self, handoff_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM handoff_records WHERE handoff_id = ?", (handoff_id,)
        ).fetchone()
        return self._decode_handoff_record(row)


    @synchronized
    def find_handoff_record(
        self, *, sha256: str, producer_run_id: str, schema_id: str, role: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM handoff_records
            WHERE sha256 = ? AND producer_run_id = ? AND schema_id = ? AND role = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (sha256, producer_run_id, schema_id, role),
        ).fetchone()
        return self._decode_handoff_record(row)


    @staticmethod
    def _decode_handoff_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
        return result


    @synchronized
    def update_handoff_state(
        self,
        handoff_id: str,
        *,
        expected_state: str,
        state: str,
        superseded_by: str | None = None,
    ) -> bool:
        cur = self._conn.execute(
            """
            UPDATE handoff_records
            SET state = ?, superseded_by = ?, updated_at = ?
            WHERE handoff_id = ? AND state = ?
            """,
            (
                state,
                superseded_by,
                datetime.now(UTC).isoformat(),
                handoff_id,
                expected_state,
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1


    @synchronized
    def get_handoff_consumption(
        self, *, consumer_run_id: str, handoff_id: str
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM handoff_consumptions
            WHERE consumer_run_id = ? AND handoff_id = ?
            """,
            (consumer_run_id, handoff_id),
        ).fetchone()
        return dict(row) if row else None


    @synchronized
    def insert_handoff_record(self, record: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO handoff_records (
                handoff_id, producer_artifact_instance_id, producer_run_id,
                producer_task_id, sha256, schema_id, schema_version, role, state,
                created_at, updated_at, superseded_by, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["handoff_id"],
                record["producer_artifact_instance_id"],
                record["producer_run_id"],
                record["producer_task_id"],
                record["sha256"],
                record["schema_id"],
                record.get("schema_version"),
                record["role"],
                record["state"],
                record["created_at"],
                record["updated_at"],
                record.get("superseded_by"),
                json.dumps(record.get("metadata") or {}, sort_keys=True, default=str),
            ),
        )
        self._conn.commit()

