"""Handoff validation at submit / pre-execution boundaries (PM0.A)."""

from __future__ import annotations

from typing import Any

from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.errors import SchemaValidationError
from product_factory.schemas.validate import validate_handoff_ref_shape


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
