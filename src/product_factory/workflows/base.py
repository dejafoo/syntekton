"""Workflow pack protocol — versioned, declarative workflow behavior (P1.G).

A `WorkflowPack` is a data-only description of a workflow's contract: its id,
version, schemas, allowed capabilities, default planner mode, validation and
skill/context policy, and routing defaults. Packs never execute
planner-supplied Python — the coordinator only ever calls its own registered
handlers, keyed by pack id, never code loaded from pack data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from product_factory.workflows.artifacts import ArtifactLandSpec


@dataclass(frozen=True)
class WorkflowPack:
    id: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    allowed_capabilities: frozenset[str]
    default_planner_mode: str
    validation_policy: dict[str, Any]
    skill_policy: dict[str, Any]
    routing_defaults: dict[str, Any]
    description: str = ""
    # Deliverables keyed by stable role; names are defaults hosts may override.
    artifacts: tuple[ArtifactLandSpec, ...] = field(default_factory=tuple)

    def content_hash(self) -> str:
        """Stable hash recorded on the run manifest to prove pack identity/version."""
        payload = {
            "id": self.id,
            "version": self.version,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "allowed_capabilities": sorted(self.allowed_capabilities),
            "default_planner_mode": self.default_planner_mode,
            "validation_policy": self.validation_policy,
            "skill_policy": self.skill_policy,
            "routing_defaults": self.routing_defaults,
            "artifacts": [spec.as_payload() for spec in self.artifacts],
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def manifest_metadata(self) -> dict[str, str]:
        return {
            "workflow_pack_id": self.id,
            "workflow_pack_version": self.version,
            "workflow_pack_hash": self.content_hash(),
        }
