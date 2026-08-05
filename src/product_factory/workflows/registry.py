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
from product_factory.workflows.change_intake import CHANGE_INTAKE_PACK
from product_factory.workflows.feasibility_discovery import FEASIBILITY_DISCOVERY_PACK
from product_factory.workflows.quality_gate import QUALITY_GATE_PACK
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK
from product_factory.workflows.repository_investigation import REPOSITORY_INVESTIGATION_PACK
from product_factory.workflows.technical_plan import TECHNICAL_PLAN_PACK
from product_factory.workflows.technical_spike import TECHNICAL_SPIKE_PACK

_PACKS: dict[str, WorkflowPack] = {
    CHANGE_INTAKE_PACK.id: CHANGE_INTAKE_PACK,
    FEASIBILITY_DISCOVERY_PACK.id: FEASIBILITY_DISCOVERY_PACK,
    QUALITY_GATE_PACK.id: QUALITY_GATE_PACK,
    REPOSITORY_CHANGE_PACK.id: REPOSITORY_CHANGE_PACK,
    REPOSITORY_INVESTIGATION_PACK.id: REPOSITORY_INVESTIGATION_PACK,
    TECHNICAL_PLAN_PACK.id: TECHNICAL_PLAN_PACK,
    TECHNICAL_SPIKE_PACK.id: TECHNICAL_SPIKE_PACK,
}

# One-release aliases: existing callers keep working while canonical pack ids
# are versioned. `code_change` → `repository_change` (P1.G);
# `architecture` → `technical_plan` (P3.D).
_ALIASES: dict[str, str] = {
    "code_change": REPOSITORY_CHANGE_PACK.id,
    "architecture": TECHNICAL_PLAN_PACK.id,
}


def _validate_pack(pack: WorkflowPack) -> None:
    from product_factory.schemas import default_schema_registry

    pack.execution_policy.validate(
        pack_id=pack.id,
        capabilities=pack.allowed_capabilities,
    )
    declared_roles = tuple(spec.role for spec in pack.artifacts)
    if tuple(pack.execution_policy.output_roles) != declared_roles:
        raise ConfigurationError(
            f"Pack {pack.id!r} execution-policy roles do not match artifacts: "
            f"{pack.execution_policy.output_roles!r} != {declared_roles!r}"
        )
    schemas = default_schema_registry()
    unknown_handoffs = sorted(
        schema_id
        for schema_id in pack.execution_policy.accepted_handoff_schemas
        if not schemas.known(schema_id)
    )
    if unknown_handoffs:
        raise ConfigurationError(
            f"Pack {pack.id!r} accepts unknown handoff schemas: {unknown_handoffs}"
        )


def register_workflow_pack(pack: WorkflowPack) -> None:
    """Register a trusted data-only pack; duplicate ids fail closed."""

    _validate_pack(pack)
    if pack.id in _PACKS:
        raise ConfigurationError(f"Workflow pack already registered: {pack.id!r}")
    _PACKS[pack.id] = pack


def canonical_workflow_id(workflow_type: str) -> str:
    return _ALIASES.get(workflow_type, workflow_type)


def is_registered_workflow(workflow_type: str) -> bool:
    return canonical_workflow_id(workflow_type) in _PACKS


def resolve_workflow_pack(workflow_type: str) -> WorkflowPack:
    canonical = canonical_workflow_id(workflow_type)
    pack = _PACKS.get(canonical)
    if pack is None:
        raise ConfigurationError(f"Unknown workflow pack: {workflow_type!r}")
    _validate_pack(pack)
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
