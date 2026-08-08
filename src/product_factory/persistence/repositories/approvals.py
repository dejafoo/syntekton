"""Action approval aggregate (authority records)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized


class ApprovalRepository(AggregateRepository):
    @synchronized
    def get_action_approval(self, approval_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM action_approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()
        return self._decode_action_approval(row)

    @synchronized
    def update_action_approval(
        self, approval_id: str, *, expected_status: str, values: dict[str, Any]
    ) -> bool:
        allowed = {
            "status",
            "actor_json",
            "decided_at",
            "consumed_at",
            "consumed_by_run_id",
            "reconciliation_json",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        if not fields:
            return False
        assignments = ", ".join(f"{key} = ?" for key in fields)
        cur = self._conn.execute(
            f"UPDATE action_approvals SET {assignments} WHERE approval_id = ? AND status = ?",
            (*fields.values(), approval_id, expected_status),
        )
        self._conn.commit()
        return cur.rowcount == 1

    @staticmethod
    def _decode_action_approval(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key in ("actor", "payload", "reconciliation"):
            result[key] = json.loads(result.pop(f"{key}_json") or "{}")
        return result

    @synchronized
    def insert_action_approval(self, approval: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO action_approvals (
                approval_id, action_type, subject_run_id, subject_artifact_instance_id,
                action_fingerprint, status, actor_json, payload_json, created_at,
                decided_at, expires_at, consumed_at, consumed_by_run_id, reconciliation_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval["approval_id"],
                approval["action_type"],
                approval["subject_run_id"],
                approval.get("subject_artifact_instance_id"),
                approval["action_fingerprint"],
                approval["status"],
                json.dumps(approval["actor"], sort_keys=True),
                json.dumps(approval.get("payload") or {}, sort_keys=True, default=str),
                approval["created_at"],
                approval.get("decided_at"),
                approval.get("expires_at"),
                approval.get("consumed_at"),
                approval.get("consumed_by_run_id"),
                json.dumps(approval.get("reconciliation") or {}, sort_keys=True, default=str),
            ),
        )
        self._conn.commit()
