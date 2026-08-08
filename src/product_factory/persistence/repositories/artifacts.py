"""Artifact blob metadata and instance aggregate."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized


class ArtifactRepository(AggregateRepository):
    @synchronized
    def list_artifact_instances(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM artifact_instances WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_artifact(self, sha256: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM artifacts WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(row) if row else None

    @synchronized
    def get_artifact_instance(self, run_id: str, sha256: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM artifact_instances
            WHERE run_id = ? AND sha256 = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, sha256),
        ).fetchone()
        return dict(row) if row else None

    @synchronized
    def get_artifact_instance_by_id(self, instance_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM artifact_instances WHERE instance_id = ?", (instance_id,)
        ).fetchone()
        return dict(row) if row else None

    @synchronized
    def record_artifact_instance(self, instance: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO artifact_instances
            (instance_id, run_id, sha256, role, content_class, producer_task_id,
             producer_tool, producer_validator, event_seq, media_type, schema_id,
             schema_version, size_bytes, display_name, classification, capture_level,
             visibility, retention, truncated, parent_instance_ids_json,
             metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                instance["instance_id"],
                instance["run_id"],
                instance["sha256"],
                instance.get("role", ""),
                instance.get("content_class"),
                instance.get("producer_task_id"),
                instance.get("producer_tool"),
                instance.get("producer_validator"),
                instance.get("event_seq"),
                instance.get("media_type", "application/octet-stream"),
                instance.get("schema_id"),
                instance.get("schema_version"),
                int(instance.get("size_bytes") or 0),
                instance.get("display_name", ""),
                instance.get("classification", "mixed"),
                instance.get("capture_level", "full"),
                instance.get("visibility", "available"),
                instance.get("retention", "run"),
                1 if instance.get("truncated") else 0,
                json.dumps(instance.get("parent_instance_ids") or []),
                json.dumps(instance.get("metadata") or {}, default=str),
                now,
            ),
        )
        self._conn.commit()

    @synchronized
    def record_artifact(self, artifact: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO artifacts
            (sha256, media_type, size_bytes, logical_name, relative_path,
             created_by_task_id, trust_level, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact["sha256"],
                artifact["media_type"],
                artifact["size_bytes"],
                artifact["logical_name"],
                artifact.get("relative_path", ""),
                artifact.get("created_by_task_id"),
                artifact.get("trust_level", "generated"),
                json.dumps(artifact.get("metadata", {})),
            ),
        )
        self._conn.commit()

    def list_artifacts(self, *, created_by_task_id: str | None = None) -> list[dict[str, Any]]:
        if created_by_task_id:
            rows = self._conn.execute(
                "SELECT * FROM artifacts WHERE created_by_task_id = ?",
                (created_by_task_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM artifacts").fetchall()
        return [dict(r) for r in rows]
