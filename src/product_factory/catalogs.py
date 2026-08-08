"""Registry-backed catalog export (SD7).

Generates JSON snapshots from trusted registries so docs/catalogs cannot drift
from runtime truth. Authority remains the registries; this module is a
projection only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory.connectors.defaults import default_connector_registry
from product_factory.registry.capability_descriptors import CAPABILITY_DESCRIPTORS
from product_factory.workflows.registry import list_workflow_packs


def workflow_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pack in list_workflow_packs():
        rows.append(
            {
                "workflow_type": pack.id,
                "version": pack.version,
                "description": pack.description,
                "allowed_capabilities": sorted(pack.allowed_capabilities),
                "artifact_roles": [spec.role for spec in pack.artifacts],
                "allowed_tool_classes": sorted(pack.execution_policy.allowed_tool_classes),
                "default_planner_mode": pack.default_planner_mode,
            }
        )
    return sorted(rows, key=lambda row: row["workflow_type"])


def capability_catalog() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for descriptor in CAPABILITY_DESCRIPTORS.values():
        rows.append(
            {
                "capability_id": descriptor.id,
                "version": descriptor.version,
                "executor_mode": descriptor.executor_mode,
                "executor_adapter": descriptor.executor_adapter_id,
                "agent_profile": descriptor.agent_profile_id,
                "model_role": descriptor.default_model_role,
                "tool_classes": sorted(descriptor.permissible_tool_classes),
                "evaluation_category": descriptor.evaluation_category,
                "result_schema": descriptor.result_schema_id,
            }
        )
    return sorted(rows, key=lambda row: row["capability_id"])


def connector_catalog() -> list[dict[str, Any]]:
    registry = default_connector_registry()
    rows: list[dict[str, Any]] = []
    for manifest in registry.manifests():
        rows.append(
            {
                "connector_id": manifest.connector_id,
                "tool_class": manifest.tool_class,
                "tools": [tool.name for tool in manifest.tools],
                "permissions": sorted(manifest.permissions),
                "egress_mode": manifest.egress.mode,
                "description": manifest.description,
            }
        )
    return sorted(rows, key=lambda row: row["connector_id"])


def full_catalog() -> dict[str, Any]:
    return {
        "workflows": workflow_catalog(),
        "capabilities": capability_catalog(),
        "connectors": connector_catalog(),
    }


def write_catalogs(output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    catalog = full_catalog()
    for name, payload in catalog.items():
        path = output_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written[name] = path
    combined = output_dir / "catalog.json"
    combined.write_text(json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    written["catalog"] = combined
    return written


__all__ = [
    "capability_catalog",
    "connector_catalog",
    "full_catalog",
    "workflow_catalog",
    "write_catalogs",
]
