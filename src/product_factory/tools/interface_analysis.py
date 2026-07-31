"""Deterministic, local-only OpenAPI and JSON Schema analysis helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.tools.policies import resolve_under_root

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


def _load(root: Path, relative_path: str) -> tuple[dict[str, Any], str]:
    path = resolve_under_root(root, relative_path)
    if not path.is_file():
        raise ToolAuthorizationError(f"Contract is not a file: {relative_path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ToolAuthorizationError(f"Invalid contract document: {relative_path}") from exc
    if not isinstance(raw, dict):
        raise ToolAuthorizationError("Contract root must be an object")
    if isinstance(raw.get("openapi"), str):
        kind = "openapi"
    elif any(key in raw for key in ("$schema", "$defs", "definitions", "properties", "type")):
        kind = "json_schema"
    else:
        raise ToolAuthorizationError("Only OpenAPI and JSON Schema contracts are supported")
    return raw, kind


def parse_contract(root: Path, relative_path: str) -> dict[str, Any]:
    contract, kind = _load(root, relative_path)
    return {
        "path": relative_path,
        "kind": kind,
        "version": contract.get("openapi") if kind == "openapi" else contract.get("$schema"),
        "title": (contract.get("info") or {}).get("title")
        if kind == "openapi"
        else contract.get("title"),
        "contract": contract,
    }


def contract_inventory(root: Path, relative_path: str) -> dict[str, Any]:
    contract, kind = _load(root, relative_path)
    addresses: list[dict[str, Any]] = []
    schemas: list[str] = []
    if kind == "openapi":
        for route, path_item in sorted((contract.get("paths") or {}).items()):
            if not isinstance(path_item, dict):
                continue
            for method, operation in sorted(path_item.items()):
                if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                addresses.append(
                    {
                        "address": f"{method.upper()} {route}",
                        "operation_id": operation.get("operationId"),
                        "tags": list(operation.get("tags") or []),
                        "summary": operation.get("summary"),
                    }
                )
        schemas = sorted((contract.get("components") or {}).get("schemas") or {})
    else:
        definitions = contract.get("$defs") or contract.get("definitions") or {}
        schemas = sorted(str(name) for name in definitions)
        for name, spec in sorted((contract.get("properties") or {}).items()):
            addresses.append(
                {
                    "address": f"$.{name}",
                    "required": name in set(contract.get("required") or []),
                    "type": spec.get("type") if isinstance(spec, dict) else None,
                }
            )
    return {
        "path": relative_path,
        "kind": kind,
        "addresses": addresses,
        "schemas": schemas,
        "address_count": len(addresses),
        "schema_count": len(schemas),
    }


def _schema_changes(
    before: dict[str, Any], after: dict[str, Any], prefix: str
) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    before_props = before.get("properties") or {}
    after_props = after.get("properties") or {}
    before_required = set(before.get("required") or [])
    after_required = set(after.get("required") or [])
    for name in sorted(set(before_props) - set(after_props)):
        changes.append(
            {"classification": "breaking", "address": f"{prefix}.{name}", "change": "removed"}
        )
    for name in sorted(set(after_props) - set(before_props)):
        classification = "breaking" if name in after_required else "non_breaking"
        changes.append(
            {"classification": classification, "address": f"{prefix}.{name}", "change": "added"}
        )
    for name in sorted((after_required - before_required) & set(before_props)):
        changes.append(
            {
                "classification": "breaking",
                "address": f"{prefix}.{name}",
                "change": "became_required",
            }
        )
    for name in sorted(set(before_props) & set(after_props)):
        old = before_props[name] if isinstance(before_props[name], dict) else {}
        new = after_props[name] if isinstance(after_props[name], dict) else {}
        if old.get("type") != new.get("type"):
            changes.append(
                {
                    "classification": "breaking",
                    "address": f"{prefix}.{name}",
                    "change": f"type_changed:{old.get('type')}->{new.get('type')}",
                }
            )
    return changes


def diff_contracts(root: Path, baseline_path: str, candidate_path: str) -> dict[str, Any]:
    before, before_kind = _load(root, baseline_path)
    after, after_kind = _load(root, candidate_path)
    if before_kind != after_kind:
        raise ToolAuthorizationError("Contracts must have the same kind")
    changes: list[dict[str, str]] = []
    if before_kind == "json_schema":
        changes.extend(_schema_changes(before, after, "$"))
    else:
        before_ops = {
            f"{method.upper()} {route}": operation
            for route, item in (before.get("paths") or {}).items()
            if isinstance(item, dict)
            for method, operation in item.items()
            if method.lower() in HTTP_METHODS and isinstance(operation, dict)
        }
        after_ops = {
            f"{method.upper()} {route}": operation
            for route, item in (after.get("paths") or {}).items()
            if isinstance(item, dict)
            for method, operation in item.items()
            if method.lower() in HTTP_METHODS and isinstance(operation, dict)
        }
        for address in sorted(set(before_ops) - set(after_ops)):
            changes.append({"classification": "breaking", "address": address, "change": "removed"})
        for address in sorted(set(after_ops) - set(before_ops)):
            changes.append(
                {"classification": "non_breaking", "address": address, "change": "added"}
            )
        before_schemas = (before.get("components") or {}).get("schemas") or {}
        after_schemas = (after.get("components") or {}).get("schemas") or {}
        for name in sorted(set(before_schemas) & set(after_schemas)):
            changes.extend(
                _schema_changes(
                    before_schemas[name], after_schemas[name], f"#/components/schemas/{name}"
                )
            )
    return {
        "kind": before_kind,
        "baseline_path": baseline_path,
        "candidate_path": candidate_path,
        "classification": (
            "breaking"
            if any(change["classification"] == "breaking" for change in changes)
            else "non_breaking"
        ),
        "changes": changes,
    }


def map_capabilities(root: Path, relative_path: str) -> dict[str, Any]:
    inventory = contract_inventory(root, relative_path)
    groups: dict[str, list[str]] = {}
    for item in inventory["addresses"]:
        tags = item.get("tags") or ["default"]
        for tag in tags:
            groups.setdefault(str(tag), []).append(str(item["address"]))
    if inventory["kind"] == "json_schema":
        groups = {"schema_fields": [str(item["address"]) for item in inventory["addresses"]]}
    return {
        "kind": inventory["kind"],
        "capabilities": [
            {"name": name, "addresses": sorted(addresses)}
            for name, addresses in sorted(groups.items())
        ],
    }


def _synthetic_value(schema: dict[str, Any]) -> Any:
    if "example" in schema:
        return schema["example"]
    if "default" in schema:
        return schema["default"]
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        return {
            name: _synthetic_value(spec) for name, spec in (schema.get("properties") or {}).items()
        }
    if kind == "array":
        return [_synthetic_value(schema.get("items") or {})]
    if kind in {"integer", "number"}:
        return 1
    if kind == "boolean":
        return True
    return "synthetic"


def generate_synthetic_fixture(
    root: Path, contract_path: str, output_path: str, schema_name: str | None = None
) -> dict[str, Any]:
    contract, kind = _load(root, contract_path)
    if kind == "openapi":
        schemas = (contract.get("components") or {}).get("schemas") or {}
        if schema_name:
            if schema_name not in schemas:
                raise ToolAuthorizationError(f"Unknown schema: {schema_name}")
            schema = schemas[schema_name]
        elif schemas:
            schema_name, schema = next(iter(schemas.items()))
        else:
            schema = {"type": "object"}
    else:
        schema = contract
    fixture = _synthetic_value(schema)
    target = resolve_under_root(root, output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": output_path, "schema": schema_name, "fixture": fixture}


def _validate_instance(instance: Any, schema: dict[str, Any], address: str = "$") -> list[str]:
    errors: list[str] = []
    kind = schema.get("type")
    valid_type = {
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
        "boolean": isinstance(instance, bool),
        "null": instance is None,
    }.get(kind if isinstance(kind, str) else "", True)
    if not valid_type:
        return [f"{address}: expected {kind}"]
    if isinstance(instance, dict):
        for name in schema.get("required") or []:
            if name not in instance:
                errors.append(f"{address}.{name}: required")
        for name, value in instance.items():
            child = (schema.get("properties") or {}).get(name)
            if isinstance(child, dict):
                errors.extend(_validate_instance(value, child, f"{address}.{name}"))
    if isinstance(instance, list) and isinstance(schema.get("items"), dict):
        for index, value in enumerate(instance):
            errors.extend(_validate_instance(value, schema["items"], f"{address}[{index}]"))
    return errors


def run_contract_simulation(
    root: Path, contract_path: str, fixture_path: str, schema_name: str | None = None
) -> dict[str, Any]:
    contract, kind = _load(root, contract_path)
    fixture_file = resolve_under_root(root, fixture_path)
    try:
        fixture = json.loads(fixture_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ToolAuthorizationError(f"Invalid JSON fixture: {fixture_path}") from exc
    if kind == "openapi":
        schemas = (contract.get("components") or {}).get("schemas") or {}
        if not schema_name or schema_name not in schemas:
            raise ToolAuthorizationError("OpenAPI simulation requires a valid schema_name")
        schema = schemas[schema_name]
    else:
        schema = contract
    errors = _validate_instance(fixture, schema)
    return {
        "status": "passed" if not errors else "failed",
        "contract_path": contract_path,
        "fixture_path": fixture_path,
        "schema": schema_name,
        "measurements": {"validation_error_count": len(errors)},
        "errors": errors,
    }
