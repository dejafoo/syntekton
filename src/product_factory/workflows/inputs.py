"""Generic typed pack input (`RunRequest.pack_input`) validation (PM1.0).

A pack declares the typed contract it needs beyond the request envelope in
`WorkflowPack.input_schema`. Hosts hand that payload over as a plain dict on
`RunRequest.pack_input`; this module validates it at submit time and fails
closed, so a malformed operator payload never costs a run. There is no
per-pack `RunRequest` field — later packs reuse this same contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows.base import WorkflowPack

# Fields a pack may declare in `input_schema` that arrive on the RunRequest
# envelope itself rather than inside `pack_input`. A pack requiring only these
# is satisfied by any submission, which keeps pre-PM1 packs unaffected.
ENVELOPE_KEYS = frozenset({"request_text", "repository_path", "validation_commands"})

_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
}


def _matches_type(value: Any, type_name: str) -> bool:
    if type_name == "null":
        return value is None
    expected = _TYPE_CHECKS.get(type_name)
    if expected is None:
        # Unrecognized JSON-schema keyword: not enforced at this boundary.
        return True
    if type_name in {"number", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def _type_error(key: str, value: Any, declared: Any) -> dict[str, Any] | None:
    if declared is None:
        return None
    names = [declared] if isinstance(declared, str) else list(declared)
    if any(_matches_type(value, str(name)) for name in names):
        return None
    return {"property": key, "expected": names, "actual": type(value).__name__}


def _is_missing(payload: dict[str, Any], key: str) -> bool:
    if key not in payload:
        return True
    value = payload[key]
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def validate_pack_input(pack: WorkflowPack, payload: Any) -> dict[str, Any]:
    """Validate `payload` against `pack.input_schema`; return a normalized copy.

    Enforces declared `required` keys, declared property types, and — when the
    schema sets `additionalProperties: false` — rejects undeclared keys.
    """
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ConfigurationError(
            "pack_input must be a JSON object",
            details={"pack_id": pack.id, "actual": type(payload).__name__},
        )

    schema = pack.input_schema or {}
    properties: dict[str, Any] = schema.get("properties") or {}
    required = [key for key in (schema.get("required") or []) if key not in ENVELOPE_KEYS]

    missing = sorted(key for key in required if _is_missing(payload, key))
    unknown: list[str] = []
    if schema.get("additionalProperties") is False:
        unknown = sorted(key for key in payload if key not in properties)
    type_errors = [
        error
        for key, value in payload.items()
        if (error := _type_error(key, value, (properties.get(key) or {}).get("type"))) is not None
    ]

    if missing or unknown or type_errors:
        parts = []
        if missing:
            parts.append(f"missing required {missing}")
        if unknown:
            parts.append(f"unknown properties {unknown}")
        if type_errors:
            parts.append(f"type mismatches {[e['property'] for e in type_errors]}")
        raise ConfigurationError(
            f"Invalid pack_input for {pack.id}: " + "; ".join(parts),
            details={
                "pack_id": pack.id,
                "missing": missing,
                "unknown": unknown,
                "type_errors": type_errors,
            },
        )
    return dict(payload)


def validate_request_pack_input(request: Any) -> dict[str, Any]:
    """Validate the `pack_input` of a run request against its resolved pack."""
    from product_factory.workflows.registry import resolve_workflow_pack

    pack = resolve_workflow_pack(getattr(request, "workflow_type", ""))
    result = validate_pack_input(pack, getattr(request, "pack_input", None))
    # PM1 discovery refuses technical spikes; WF1.A is a later companion pack.
    if pack.id == "feasibility_discovery" and result.get("allow_technical_spike") is True:
        raise ConfigurationError(
            "allow_technical_spike is not supported in PM1 feasibility_discovery",
            details={"pack_id": pack.id, "allow_technical_spike": True},
        )
    return result


def persist_pack_input(pack_input: dict[str, Any] | None, input_dir: Path) -> Path:
    """Pin the typed payload beside the request so resume and inspection see it."""
    input_dir.mkdir(parents=True, exist_ok=True)
    path = input_dir / "pack-input.json"
    path.write_text(
        json.dumps(pack_input or {}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def parse_pack_input_option(raw: str | None) -> dict[str, Any]:
    """Parse a `--pack-input` CLI value: inline JSON object or `@path.json`."""
    if raw is None:
        return {}
    text = raw.strip()
    if not text:
        return {}
    if text.startswith("@"):
        path = Path(text[1:]).expanduser()
        if not path.is_file():
            raise ConfigurationError(
                f"pack_input file not found: {path}",
                details={"path": str(path)},
            )
        text = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"pack_input is not valid JSON: {exc}",
            details={"error": str(exc)},
        ) from None
    if not isinstance(parsed, dict):
        raise ConfigurationError(
            "pack_input must be a JSON object",
            details={"actual": type(parsed).__name__},
        )
    return parsed
