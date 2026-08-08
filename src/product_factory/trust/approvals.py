"""Durable approval authority for externally visible actions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.persistence.database import Database

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "revoked", "consumed"]


class ApprovalError(Exception):
    """A durable action approval cannot authorize the requested action."""


class ActionApproval(BaseModel):
    approval_id: str
    action_type: str
    subject_run_id: str
    subject_artifact_instance_id: str | None = None
    action_fingerprint: str = Field(min_length=64, max_length=64)
    status: ApprovalStatus
    actor: dict[str, str]
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    decided_at: str | None = None
    expires_at: str | None = None
    consumed_at: str | None = None
    consumed_by_run_id: str | None = None
    reconciliation: dict[str, Any] = Field(default_factory=dict)


def deployment_action_fingerprint(
    *,
    release_handoff_id: str,
    release_handoff_digest: str,
    release_plan_digest: str,
    artifact_digest: str,
    target_id: str,
    change_window: Any,
    idempotency_key: str,
) -> str:
    """Hash the complete canonical deployment authority binding."""
    payload = {
        "artifact_digest": artifact_digest,
        "change_window": change_window,
        "idempotency_key": idempotency_key,
        "release_handoff_digest": release_handoff_digest,
        "release_handoff_id": release_handoff_id,
        "release_plan_digest": release_plan_digest,
        "target_id": target_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class ApprovalService:
    """Owns the atomic approval state machine and one-time consumption."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def create_pending(
        self,
        *,
        action_type: str,
        subject_run_id: str,
        action_fingerprint: str,
        actor: dict[str, Any] | str,
        subject_artifact_instance_id: str | None = None,
        payload: dict[str, Any] | None = None,
        expires_at: str | None = None,
    ) -> ActionApproval:
        now = datetime.now(UTC).isoformat()
        approval = ActionApproval(
            approval_id=f"approval-{uuid.uuid4().hex}",
            action_type=action_type,
            subject_run_id=subject_run_id,
            subject_artifact_instance_id=subject_artifact_instance_id,
            action_fingerprint=action_fingerprint,
            status="pending",
            actor=_safe_actor(actor),
            payload=payload or {},
            created_at=now,
            expires_at=expires_at,
        )
        self.db.insert_action_approval(approval.model_dump(mode="json"))
        return approval

    def get(self, approval_id: str) -> ActionApproval | None:
        raw = self.db.get_action_approval(approval_id)
        return ActionApproval.model_validate(raw) if raw else None

    def decide(
        self,
        approval_id: str,
        decision: Literal["approved", "rejected"],
        actor: dict[str, Any] | str,
    ) -> ActionApproval:
        if decision not in {"approved", "rejected"}:
            raise ApprovalError("Decision must be approved or rejected")
        current = self._required(approval_id)
        if self._is_expired(current):
            self._expire(current)
            raise ApprovalError("Approval expired before decision")
        if not self.db.update_action_approval(
            approval_id,
            expected_status="pending",
            values={
                "status": decision,
                "actor_json": json.dumps(_safe_actor(actor), sort_keys=True),
                "decided_at": datetime.now(UTC).isoformat(),
            },
        ):
            raise ApprovalError("Approval is no longer pending")
        return self._required(approval_id)

    def revoke(self, approval_id: str, *, actor: dict[str, Any] | str) -> ActionApproval:
        current = self._required(approval_id)
        if current.status not in {"pending", "approved"}:
            raise ApprovalError(f"Cannot revoke {current.status!r} approval")
        if not self.db.update_action_approval(
            approval_id,
            expected_status=current.status,
            values={
                "status": "revoked",
                "actor_json": json.dumps(_safe_actor(actor), sort_keys=True),
                "decided_at": datetime.now(UTC).isoformat(),
            },
        ):
            raise ApprovalError("Approval state changed while revoking")
        return self._required(approval_id)

    def consume_for_execution(
        self, approval_id: str, *, expected_fingerprint: str, consumer_run_id: str
    ) -> ActionApproval:
        current = self._required(approval_id)
        if self._is_expired(current):
            self._expire(current)
            raise ApprovalError("Approval has expired")
        if current.status != "approved":
            raise ApprovalError(f"Approval is not executable: {current.status}")
        if current.action_fingerprint != expected_fingerprint:
            raise ApprovalError("Approval fingerprint does not match requested action")
        if not self.db.update_action_approval(
            approval_id,
            expected_status="approved",
            values={
                "status": "consumed",
                "consumed_at": datetime.now(UTC).isoformat(),
                "consumed_by_run_id": consumer_run_id,
            },
        ):
            raise ApprovalError("Approval was already consumed or changed")
        return self._required(approval_id)

    def _required(self, approval_id: str) -> ActionApproval:
        approval = self.get(approval_id)
        if approval is None:
            raise ApprovalError(f"Unknown approval: {approval_id}")
        return approval

    @staticmethod
    def _is_expired(approval: ActionApproval) -> bool:
        if not approval.expires_at:
            return False
        return datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00")) <= datetime.now(
            UTC
        )

    def _expire(self, approval: ActionApproval) -> None:
        if approval.status in {"pending", "approved"}:
            self.db.update_action_approval(
                approval.approval_id, expected_status=approval.status, values={"status": "expired"}
            )


def _safe_actor(actor: dict[str, Any] | str) -> dict[str, str]:
    if isinstance(actor, str):
        return {"kind": "local_operator", "id": actor}
    kind = str(actor.get("kind") or "local_operator")
    if kind not in {"local_operator", "token_fingerprint"}:
        raise ApprovalError("Actor must be local_operator or token_fingerprint")
    identifier = str(actor.get("id") or actor.get("fingerprint") or "")
    if not identifier or "bearer" in identifier.lower():
        raise ApprovalError("Actor identity must be non-secret")
    return {"kind": kind, "id": identifier}
