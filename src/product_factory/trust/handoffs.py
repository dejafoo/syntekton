"""Durable, authority-resolved cross-run handoffs."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Container
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.persistence.database import Database
from product_factory.workflows.handoffs import extract_handoff_refs, validate_pack_handoffs

HandoffState = Literal["draft", "evidence_complete", "approved", "superseded"]


class HandoffError(Exception):
    """A durable handoff operation failed."""


class HandoffRefusal(HandoffError):
    """An untrusted handoff assertion did not match durable authority."""


class HandoffRecord(BaseModel):
    handoff_id: str
    producer_artifact_instance_id: str
    producer_run_id: str
    producer_task_id: str
    sha256: str = Field(min_length=64, max_length=64)
    schema_id: str
    schema_version: str | None = None
    role: str
    state: HandoffState
    created_at: str
    updated_at: str
    superseded_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HandoffConsumption(BaseModel):
    consumer_run_id: str
    handoff_id: str
    producer_artifact_instance_id: str
    consumer_artifact_instance_id: str
    state_at_resolution: HandoffState
    resolved_at: str


class ResolvedHandoff(BaseModel):
    record: HandoffRecord
    consumption: HandoffConsumption
    consumer_artifact_instance: dict[str, Any]
    materialized_path: Path | None = None


class HandoffService:
    """Owns handoff authority, lineage, and verified materialization."""

    def __init__(self, db: Database, artifact_store_root: Path | None = None) -> None:
        self.db = db
        self.artifact_store_root = artifact_store_root

    def create_from_artifact_instance(
        self,
        instance_id: str,
        *,
        role: str,
        state: HandoffState = "draft",
        metadata: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        if state != "draft":
            raise HandoffError("Handoffs must be created in draft state")
        instance = self.db.get_artifact_instance_by_id(instance_id)
        if instance is None:
            raise HandoffRefusal(f"Unknown persisted artifact instance: {instance_id}")
        producer_task_id = instance.get("producer_task_id")
        schema_id = instance.get("schema_id")
        if not producer_task_id or not schema_id:
            raise HandoffRefusal("Artifact instance lacks producer task or schema identity")
        now = datetime.now(UTC).isoformat()
        record = HandoffRecord(
            handoff_id=f"handoff-{uuid.uuid4().hex}",
            producer_artifact_instance_id=instance_id,
            producer_run_id=str(instance["run_id"]),
            producer_task_id=str(producer_task_id),
            sha256=str(instance["sha256"]),
            schema_id=str(schema_id),
            schema_version=instance.get("schema_version"),
            role=role,
            state=state,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        self.db.insert_handoff_record(record.model_dump(mode="json"))
        return record

    def promote_evidence_complete(self, handoff_id: str) -> HandoffRecord:
        return self._transition(handoff_id, expected="draft", target="evidence_complete")

    def approve(self, handoff_id: str, *, actor: dict[str, Any] | str) -> HandoffRecord:
        record = self._transition(handoff_id, expected="evidence_complete", target="approved")
        record.metadata["approved_by"] = _safe_actor(actor)
        return record

    def supersede(
        self,
        handoff_id: str,
        *,
        successor_handoff_id: str | None,
        actor: dict[str, Any] | str,
    ) -> HandoffRecord:
        if (
            successor_handoff_id is not None
            and self.db.get_handoff_record(successor_handoff_id) is None
        ):
            raise HandoffRefusal(f"Unknown successor handoff: {successor_handoff_id}")
        record = self._transition(
            handoff_id,
            expected="approved",
            target="superseded",
            superseded_by=successor_handoff_id,
        )
        record.metadata["superseded_by_actor"] = _safe_actor(actor)
        return record

    def resolve_refs(
        self,
        request_like: Any,
        pack: Any,
        *,
        consumer_run_id: str,
        materialize_dir: Path | None = None,
    ) -> list[ResolvedHandoff]:
        validate_pack_handoffs(request_like, pack)
        accepted_states = set(
            pack.execution_policy.accepted_handoff_states or ("approved", "evidence_complete")
        )
        resolved: list[ResolvedHandoff] = []
        for raw_ref in extract_handoff_refs(request_like):
            metadata = raw_ref.get("metadata") or {}
            handoff_id = metadata.get("handoff_id") if isinstance(metadata, dict) else None
            record_raw = (
                self.db.get_handoff_record(str(handoff_id))
                if handoff_id
                else self.db.find_handoff_record(
                    sha256=str(raw_ref["digest"]),
                    producer_run_id=str(raw_ref["producer_run_id"]),
                    schema_id=str(raw_ref["schema_id"]),
                    role=str(raw_ref["role"]),
                )
            )
            if record_raw is None:
                raise HandoffRefusal("No durable handoff record matches supplied reference")
            record = HandoffRecord.model_validate(record_raw)
            self._assert_matches(record, raw_ref, accepted_states)
            producer = self.db.get_artifact_instance_by_id(record.producer_artifact_instance_id)
            if producer is None or str(producer["sha256"]) != record.sha256:
                raise HandoffRefusal("Handoff producer artifact lineage is missing or inconsistent")
            content = self._verified_bytes(record)
            materialized_path = self._materialize(record, content, materialize_dir)
            existing = self.db.get_handoff_consumption(
                consumer_run_id=consumer_run_id, handoff_id=record.handoff_id
            )
            if existing is not None:
                child = self.db.get_artifact_instance_by_id(
                    str(existing["consumer_artifact_instance_id"])
                )
                if child is None:
                    raise HandoffRefusal("Existing handoff consumption has missing child lineage")
                resolved.append(
                    ResolvedHandoff(
                        record=record,
                        consumption=HandoffConsumption.model_validate(existing),
                        consumer_artifact_instance=child,
                        materialized_path=materialized_path,
                    )
                )
                continue
            child = self._child_instance(record, producer, consumer_run_id)
            self.db.record_artifact_instance(child)
            consumption = HandoffConsumption(
                consumer_run_id=consumer_run_id,
                handoff_id=record.handoff_id,
                producer_artifact_instance_id=record.producer_artifact_instance_id,
                consumer_artifact_instance_id=str(child["instance_id"]),
                state_at_resolution=record.state,
                resolved_at=datetime.now(UTC).isoformat(),
            )
            try:
                self.db.insert_handoff_consumption(consumption.model_dump(mode="json"))
            except Exception as exc:
                raise HandoffRefusal("Handoff was already consumed by this run") from exc
            resolved.append(
                ResolvedHandoff(
                    record=record,
                    consumption=consumption,
                    consumer_artifact_instance=child,
                    materialized_path=materialized_path,
                )
            )
        return resolved

    def _transition(
        self,
        handoff_id: str,
        *,
        expected: HandoffState,
        target: HandoffState,
        superseded_by: str | None = None,
    ) -> HandoffRecord:
        if not self.db.update_handoff_state(
            handoff_id,
            expected_state=expected,
            state=target,
            superseded_by=superseded_by,
        ):
            raise HandoffRefusal(f"Handoff {handoff_id} is not in {expected!r} state")
        record = self.db.get_handoff_record(handoff_id)
        if record is None:
            raise HandoffError(f"Handoff disappeared after transition: {handoff_id}")
        return HandoffRecord.model_validate(record)

    @staticmethod
    def _assert_matches(
        record: HandoffRecord, raw: dict[str, Any], accepted_states: Container[str]
    ) -> None:
        assertions = {
            "producer_run_id": record.producer_run_id,
            "producer_task_id": record.producer_task_id,
            "digest": record.sha256,
            "schema_id": record.schema_id,
            "role": record.role,
        }
        for field, authoritative in assertions.items():
            if str(raw.get(field) or "") != str(authoritative):
                raise HandoffRefusal(f"Handoff {field} assertion does not match durable record")
        if record.state not in accepted_states or record.state == "superseded":
            raise HandoffRefusal(f"Handoff state {record.state!r} is not accepted by this pack")

    def _verified_bytes(self, record: HandoffRecord) -> bytes:
        if self.artifact_store_root is None:
            raise HandoffRefusal("Artifact store root is required for handoff resolution")
        candidates = (
            self.artifact_store_root
            / "runs"
            / record.producer_run_id
            / "artifacts"
            / "blobs"
            / record.sha256,
            self.artifact_store_root / "artifacts" / "blobs" / record.sha256,
        )
        blob = next((path for path in candidates if path.is_file()), None)
        if blob is None:
            raise HandoffRefusal("Verified handoff artifact blob is unavailable")
        content = blob.read_bytes()
        if hashlib.sha256(content).hexdigest() != record.sha256:
            raise HandoffRefusal("Handoff artifact digest does not match durable record")
        return content

    @staticmethod
    def _materialize(record: HandoffRecord, content: bytes, directory: Path | None) -> Path | None:
        if directory is None:
            return None
        target = directory / "handoffs" / record.handoff_id
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        return target

    @staticmethod
    def _child_instance(
        record: HandoffRecord, producer: dict[str, Any], consumer_run_id: str
    ) -> dict[str, Any]:
        parents = [record.producer_artifact_instance_id]
        return {
            "instance_id": f"artifact-instance-{uuid.uuid4().hex}",
            "run_id": consumer_run_id,
            "sha256": record.sha256,
            "role": record.role,
            "content_class": producer.get("content_class"),
            "producer_task_id": record.producer_task_id,
            "media_type": producer.get("media_type"),
            "schema_id": record.schema_id,
            "schema_version": record.schema_version,
            "size_bytes": producer.get("size_bytes"),
            "display_name": producer.get("display_name") or record.handoff_id,
            "classification": producer.get("classification", "mixed"),
            "capture_level": producer.get("capture_level", "full"),
            "visibility": producer.get("visibility", "available"),
            "retention": producer.get("retention", "run"),
            "truncated": bool(producer.get("truncated")),
            "parent_instance_ids": parents,
            "metadata": {"resolved_handoff_id": record.handoff_id},
        }


def _safe_actor(actor: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(actor, str):
        return {"kind": "local_operator", "id": actor}
    kind = str(actor.get("kind") or "local_operator")
    if kind not in {"local_operator", "token_fingerprint"}:
        raise HandoffRefusal("Actor must be local_operator or token_fingerprint")
    return {"kind": kind, "id": str(actor.get("id") or "")}
