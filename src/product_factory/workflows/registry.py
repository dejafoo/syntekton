"""Workflow pack registry — coordinator loads packs by id, never by raw code."""

from __future__ import annotations

from product_factory.domain.errors import ConfigurationError
from product_factory.workflows.base import WorkflowPack
from product_factory.workflows.repository_change import REPOSITORY_CHANGE_PACK

_PACKS: dict[str, WorkflowPack] = {
    REPOSITORY_CHANGE_PACK.id: REPOSITORY_CHANGE_PACK,
}

# `code_change` is a one-release alias for `repository_change` (P1.G): existing
# callers keep working unchanged while the canonical pack id is versioned.
_ALIASES: dict[str, str] = {
    "code_change": REPOSITORY_CHANGE_PACK.id,
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
