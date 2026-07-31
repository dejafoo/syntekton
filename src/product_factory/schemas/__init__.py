"""Shared versioned schema contracts (PM0)."""

from product_factory.schemas.builtin import (
    LEGACY_OUTPUT_SCHEMA_MAP,
    ROLE_TO_SCHEMA,
    resolve_output_schema_id,
    seed_builtin_schemas,
)
from product_factory.schemas.kinds import HANDOFF_STATE, SchemaKind
from product_factory.schemas.registry import (
    SchemaRegistry,
    SchemaSpec,
    default_schema_registry,
    reset_default_schema_registry,
)
from product_factory.schemas.validate import (
    assert_schema_writable,
    read_schema_metadata,
    validate_handoff_ref_shape,
    validate_write_payload,
)

__all__ = [
    "HANDOFF_STATE",
    "LEGACY_OUTPUT_SCHEMA_MAP",
    "ROLE_TO_SCHEMA",
    "SchemaKind",
    "SchemaRegistry",
    "SchemaSpec",
    "assert_schema_writable",
    "default_schema_registry",
    "read_schema_metadata",
    "reset_default_schema_registry",
    "resolve_output_schema_id",
    "seed_builtin_schemas",
    "validate_handoff_ref_shape",
    "validate_write_payload",
]
