"""Handoff validation at submit / pre-execution boundaries (PM0.A)."""

from __future__ import annotations

from typing import Any

from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.errors import SchemaValidationError
from product_factory.schemas.validate import validate_handoff_ref_shape
from product_factory.workflows.base import WorkflowPack


def extract_handoff_refs(request_like: Any) -> list[dict[str, Any]]:
    metadata = getattr(request_like, "metadata", None) or {}
    if isinstance(request_like, dict):
        metadata = request_like.get("metadata") or {}
        direct = request_like.get("handoff_refs")
    else:
        direct = getattr(request_like, "handoff_refs", None)
    raw = direct if direct is not None else metadata.get("handoff_refs")
    if not raw:
        return []
    if not isinstance(raw, list):
        raise SchemaValidationError(
            "handoff_refs must be a list",
            details={"type": type(raw).__name__},
        )
    return [item if isinstance(item, dict) else dict(item) for item in raw]


def validate_request_handoffs(request_like: Any) -> list[HandoffRef]:
    """Fail closed on incompatible or malformed handoff refs."""
    refs: list[HandoffRef] = []
    for item in extract_handoff_refs(request_like):
        validate_handoff_ref_shape(item)
        ref = HandoffRef.model_validate(item)
        # Reserved schemas may be referenced for read, but not as approved inputs yet.
        if ref.state == "approved" and ref.schema_id.endswith(
            (
                "feasibility_dossier.v1",
                "change_brief.v1",
                "spike_result.v1",
                "verification_report.v1",
                "release_plan.v1",
                "deployment_record.v1",
                "operational_record.v1",
            )
        ):
            # Allow shape validation; packs that consume these land in PM1+.
            pass
        refs.append(ref)
    return refs


def validate_pack_handoffs(
    request_like: Any,
    pack: WorkflowPack,
) -> list[HandoffRef]:
    """Validate supplied pins against a pack's declared consumer contract."""
    refs = validate_request_handoffs(request_like)
    accepted = set(pack.validation_policy.get("accepted_handoff_schemas") or [])
    if not accepted:
        return refs
    accepted_states = set(
        pack.validation_policy.get("accepted_handoff_states")
        or ["approved", "evidence_complete"]
    )
    accepted_roles = pack.validation_policy.get("accepted_handoff_roles") or {}
    for ref in refs:
        if ref.schema_id not in accepted:
            raise SchemaValidationError(
                f"{pack.id} does not accept handoff schema {ref.schema_id!r}",
                details={
                    "pack_id": pack.id,
                    "schema_id": ref.schema_id,
                    "accepted_schemas": sorted(accepted),
                },
            )
        if ref.state not in accepted_states:
            raise SchemaValidationError(
                f"{pack.id} cannot consume a {ref.state!r} handoff",
                details={
                    "pack_id": pack.id,
                    "schema_id": ref.schema_id,
                    "state": ref.state,
                    "accepted_states": sorted(accepted_states),
                },
            )
        roles = set(accepted_roles.get(ref.schema_id) or [])
        if roles and ref.role not in roles:
            raise SchemaValidationError(
                f"Handoff role {ref.role!r} does not match schema {ref.schema_id!r}",
                details={
                    "pack_id": pack.id,
                    "schema_id": ref.schema_id,
                    "role": ref.role,
                    "accepted_roles": sorted(roles),
                },
            )
    return refs
