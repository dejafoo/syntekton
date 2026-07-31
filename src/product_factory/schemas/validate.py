"""Schema validation helpers for compiler and submit boundaries."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import SchemaValidationError
from product_factory.schemas.builtin import resolve_output_schema_id
from product_factory.schemas.registry import SchemaRegistry, default_schema_registry


def assert_schema_writable(
    schema_id: str,
    *,
    registry: SchemaRegistry | None = None,
) -> str:
    """Resolve legacy aliases and require a non-reserved registered schema."""
    resolved = resolve_output_schema_id(schema_id)
    reg = registry or default_schema_registry()
    reg.require(resolved, for_write=True)
    return resolved


def validate_write_payload(
    schema_id: str,
    payload: Any,
    *,
    registry: SchemaRegistry | None = None,
) -> str:
    resolved = resolve_output_schema_id(schema_id)
    reg = registry or default_schema_registry()
    reg.validate_payload(resolved, payload, for_write=True)
    return resolved


def read_schema_metadata(
    schema_id: str | None,
    *,
    registry: SchemaRegistry | None = None,
) -> dict[str, Any]:
    """Tolerant reader path: unknown ids stay opaque with a warning flag."""
    if not schema_id:
        return {"schema_id": None, "known": False, "warning": "missing_schema_id"}
    reg = registry or default_schema_registry()
    spec = reg.get(schema_id)
    if spec is None:
        return {
            "schema_id": schema_id,
            "known": False,
            "warning": "unknown_schema_id",
            "opaque": True,
        }
    return {
        "schema_id": schema_id,
        "known": True,
        "kind": spec.kind,
        "version": spec.version,
        "reserved": spec.reserved,
        "opaque": False,
    }


def validate_handoff_ref_shape(ref: dict[str, Any]) -> None:
    required = ("schema_id", "digest", "producer_run_id", "producer_task_id", "role", "state")
    missing = [key for key in required if not ref.get(key)]
    if missing:
        raise SchemaValidationError(
            f"HandoffRef missing required fields: {missing}",
            details={"missing": missing},
        )
    state = ref["state"]
    if state not in {"draft", "evidence_complete", "approved", "superseded"}:
        raise SchemaValidationError(
            f"Invalid handoff_state {state!r}",
            details={"state": state},
        )
    default_schema_registry().require(str(ref["schema_id"]), for_write=False)
