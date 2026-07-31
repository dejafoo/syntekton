"""Workflow pack registry — coordinator loads packs by id, never by raw code."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows.artifacts import (
    ArtifactLandMap,
    ArtifactOverrideError,
    normalize_overrides,
    resolve_artifact_land_map,
)
from product_factory.workflows.base import WorkflowPack
from product_factory.workflows.feasibility_discovery import FEASIBILITY_DISCOVERY_PACK
from product_factory.workflows.quality_gate import QUALITY_GATE_PACK
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK
from product_factory.workflows.repository_investigation import REPOSITORY_INVESTIGATION_PACK
from product_factory.workflows.technical_plan import TECHNICAL_PLAN_PACK

_PACKS: dict[str, WorkflowPack] = {
    FEASIBILITY_DISCOVERY_PACK.id: FEASIBILITY_DISCOVERY_PACK,
    QUALITY_GATE_PACK.id: QUALITY_GATE_PACK,
    REPOSITORY_CHANGE_PACK.id: REPOSITORY_CHANGE_PACK,
    REPOSITORY_INVESTIGATION_PACK.id: REPOSITORY_INVESTIGATION_PACK,
    TECHNICAL_PLAN_PACK.id: TECHNICAL_PLAN_PACK,
}

# One-release aliases: existing callers keep working while canonical pack ids
# are versioned. `code_change` → `repository_change` (P1.G);
# `architecture` → `technical_plan` (P3.D).
_ALIASES: dict[str, str] = {
    "code_change": REPOSITORY_CHANGE_PACK.id,
    "architecture": TECHNICAL_PLAN_PACK.id,
}


def canonical_workflow_id(workflow_type: str) -> str:
    return _ALIASES.get(workflow_type, workflow_type)


def resolve_workflow_pack(workflow_type: str) -> WorkflowPack:
    canonical = canonical_workflow_id(workflow_type)
    pack = _PACKS.get(canonical)
    if pack is None:
        raise ConfigurationError(f"Unknown workflow pack: {workflow_type!r}")
    return pack


def list_workflow_packs() -> list[WorkflowPack]:
    return list(_PACKS.values())


def overrides_from_request(request: Any) -> dict[str, Any]:
    """Collect artifact overrides from every accepted request surface.

    Precedence within the request: typed `artifact_overrides`, then the
    `artifact_overrides` metadata passthrough (used by thin hosts), then the
    deprecated `requested_artifacts` list.
    """
    merged: dict[str, Any] = {}
    metadata = getattr(request, "metadata", None) or {}
    for source in (
        getattr(request, "requested_artifacts", None),
        metadata.get("artifact_overrides"),
        getattr(request, "artifact_overrides", None),
    ):
        if not source:
            continue
        if isinstance(source, dict):
            source = {
                role: (spec.model_dump(exclude_none=True) if hasattr(spec, "model_dump") else spec)
                for role, spec in source.items()
            }
        merged.update(normalize_overrides(source))
    return merged


def land_map_for_request(
    request: Any,
    *,
    planner_artifacts: Any = None,
) -> ArtifactLandMap:
    """Resolve the deliverable land map for a run request.

    Raises `ConfigurationError` for unknown roles or unsafe destinations so a
    bad override fails at submit time rather than after a paid run.
    """
    pack = resolve_workflow_pack(getattr(request, "workflow_type", ""))
    try:
        return resolve_artifact_land_map(
            pack.artifacts,
            overrides=overrides_from_request(request),
            planner_artifacts=planner_artifacts,
        )
    except ArtifactOverrideError as exc:
        raise ConfigurationError(str(exc)) from None
