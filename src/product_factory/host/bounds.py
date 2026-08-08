"""Strict request bounds for product-factory.host/v2 mutation bodies."""

from __future__ import annotations

import json
from typing import Any

# Conservative defaults for a single-operator local/remote host. Tune via
# PRODUCT_FACTORY_HOST_BOUNDS only in tests — production ships these ceilings.
MAX_REQUEST_TEXT_CHARS = 200_000
MAX_REQUEST_BODY_BYTES = 512_000
MAX_PACK_INPUT_DEPTH = 8
MAX_PACK_INPUT_NODES = 500
MAX_PACK_INPUT_JSON_BYTES = 64_000
MAX_HANDOFF_CLAIMS = 32
MAX_VALIDATION_COMMANDS = 32
MAX_ARTIFACT_OVERRIDES = 64
MAX_METADATA_ENTRIES = 64
MAX_METADATA_VALUE_CHARS = 2_000
MAX_NOTE_CHARS = 8_000


class BoundViolation(ValueError):
    """A mutation body exceeded a published host/v2 bound."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _count_nodes(value: Any, *, depth: int = 0) -> tuple[int, int]:
    """Return (max_depth, node_count) for a JSON-like value."""
    if not isinstance(value, (dict, list)):
        return depth, 1
    if isinstance(value, dict):
        if not value:
            return depth, 1
        deepest = depth
        total = 1
        for child in value.values():
            child_depth, child_count = _count_nodes(child, depth=depth + 1)
            deepest = max(deepest, child_depth)
            total += child_count
        return deepest, total
    if not value:
        return depth, 1
    deepest = depth
    total = 1
    for child in value:
        child_depth, child_count = _count_nodes(child, depth=depth + 1)
        deepest = max(deepest, child_depth)
        total += child_count
    return deepest, total


def enforce_request_text(text: str) -> None:
    if len(text) > MAX_REQUEST_TEXT_CHARS:
        raise BoundViolation(
            "request_too_large",
            f"request_text exceeds {MAX_REQUEST_TEXT_CHARS} characters",
            details={"chars": len(text), "max": MAX_REQUEST_TEXT_CHARS},
        )


def enforce_pack_input(pack_input: dict[str, Any]) -> None:
    encoded = json.dumps(pack_input, separators=(",", ":"), default=str).encode("utf-8")
    if len(encoded) > MAX_PACK_INPUT_JSON_BYTES:
        raise BoundViolation(
            "pack_input_too_large",
            f"pack_input exceeds {MAX_PACK_INPUT_JSON_BYTES} bytes",
            details={"bytes": len(encoded), "max": MAX_PACK_INPUT_JSON_BYTES},
        )
    depth, nodes = _count_nodes(pack_input)
    if depth > MAX_PACK_INPUT_DEPTH:
        raise BoundViolation(
            "pack_input_too_deep",
            f"pack_input depth exceeds {MAX_PACK_INPUT_DEPTH}",
            details={"depth": depth, "max": MAX_PACK_INPUT_DEPTH},
        )
    if nodes > MAX_PACK_INPUT_NODES:
        raise BoundViolation(
            "pack_input_too_wide",
            f"pack_input node count exceeds {MAX_PACK_INPUT_NODES}",
            details={"nodes": nodes, "max": MAX_PACK_INPUT_NODES},
        )


def enforce_list_bound(name: str, items: list[Any], maximum: int) -> None:
    if len(items) > maximum:
        raise BoundViolation(
            f"{name}_too_many",
            f"{name} count exceeds {maximum}",
            details={"count": len(items), "max": maximum},
        )


def enforce_mapping_bound(name: str, mapping: dict[str, Any], maximum: int) -> None:
    if len(mapping) > maximum:
        raise BoundViolation(
            f"{name}_too_many",
            f"{name} count exceeds {maximum}",
            details={"count": len(mapping), "max": maximum},
        )


def enforce_metadata(metadata: dict[str, str]) -> None:
    enforce_mapping_bound("metadata", metadata, MAX_METADATA_ENTRIES)
    for key, value in metadata.items():
        if len(value) > MAX_METADATA_VALUE_CHARS:
            raise BoundViolation(
                "metadata_value_too_large",
                f"metadata[{key!r}] exceeds {MAX_METADATA_VALUE_CHARS} characters",
                details={"key": key, "chars": len(value), "max": MAX_METADATA_VALUE_CHARS},
            )


def enforce_note(note: str) -> None:
    if len(note) > MAX_NOTE_CHARS:
        raise BoundViolation(
            "note_too_large",
            f"note exceeds {MAX_NOTE_CHARS} characters",
            details={"chars": len(note), "max": MAX_NOTE_CHARS},
        )


def enforce_body_bytes(raw: bytes | str) -> None:
    size = len(raw.encode("utf-8")) if isinstance(raw, str) else len(raw)
    if size > MAX_REQUEST_BODY_BYTES:
        raise BoundViolation(
            "body_too_large",
            f"request body exceeds {MAX_REQUEST_BODY_BYTES} bytes",
            details={"bytes": size, "max": MAX_REQUEST_BODY_BYTES},
        )
