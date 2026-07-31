"""Versioned artifact / handoff schema registry (PM0)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from product_factory.domain.errors import ConfigurationError, SchemaValidationError
from product_factory.schemas.kinds import SchemaKind


@dataclass(frozen=True)
class SchemaSpec:
    """One registered content contract."""

    id: str
    version: str
    kind: SchemaKind
    # Minimal JSON-schema-like dict; required keys checked by validate_payload.
    json_schema: dict[str, Any] = field(default_factory=dict)
    # When True, writers may not yet emit this schema (PM1+ stubs).
    reserved: bool = False
    description: str = ""

    @property
    def full_id(self) -> str:
        return self.id if self.id.endswith(f".v{self.version.lstrip('v')}") else self.id


class SchemaRegistry:
    """Fail-closed writes; tolerant reads for unknown ids."""

    def __init__(self) -> None:
        self._specs: dict[str, SchemaSpec] = {}

    def register(self, spec: SchemaSpec) -> None:
        if spec.id in self._specs:
            raise ConfigurationError(f"Schema already registered: {spec.id}")
        self._specs[spec.id] = spec

    def get(self, schema_id: str) -> SchemaSpec | None:
        return self._specs.get(schema_id)

    def require(self, schema_id: str, *, for_write: bool = True) -> SchemaSpec:
        spec = self.get(schema_id)
        if spec is None:
            raise SchemaValidationError(
                f"Unknown schema_id {schema_id!r}",
                details={"schema_id": schema_id},
            )
        if for_write and spec.reserved:
            raise SchemaValidationError(
                f"Schema {schema_id!r} is reserved for a later phase",
                details={"schema_id": schema_id, "reserved": True},
            )
        return spec

    def known(self, schema_id: str) -> bool:
        return schema_id in self._specs

    def list_ids(self) -> list[str]:
        return sorted(self._specs)

    def validate_payload(self, schema_id: str, payload: Any, *, for_write: bool = True) -> None:
        spec = self.require(schema_id, for_write=for_write)
        if not isinstance(payload, dict):
            # Document / patch schemas accept opaque string bodies.
            if spec.kind in {"handoff", "task_output"} and isinstance(payload, str):
                return
            raise SchemaValidationError(
                f"Payload for {schema_id} must be an object or document string",
                details={"schema_id": schema_id, "type": type(payload).__name__},
            )
        required = list(spec.json_schema.get("required") or [])
        missing = [key for key in required if key not in payload]
        if missing:
            raise SchemaValidationError(
                f"Payload for {schema_id} missing required fields: {missing}",
                details={"schema_id": schema_id, "missing": missing},
            )


_DEFAULT: SchemaRegistry | None = None


def default_schema_registry() -> SchemaRegistry:
    global _DEFAULT
    if _DEFAULT is None:
        from product_factory.schemas.builtin import seed_builtin_schemas

        registry = SchemaRegistry()
        seed_builtin_schemas(registry)
        _DEFAULT = registry
    return _DEFAULT


def reset_default_schema_registry() -> None:
    """Test helper to clear the process-wide registry."""
    global _DEFAULT
    _DEFAULT = None
