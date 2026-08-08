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
from typing import Any, Literal

from product_factory.domain.capabilities import CAPABILITIES, CAPABILITY_TOOL_CLASSES
from product_factory.domain.errors import ConfigurationError
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    KNOWN_AGENT_PROFILES,
    KNOWN_EXECUTOR_ADAPTERS,
    require_descriptor,
)
from product_factory.workflows.artifacts import ArtifactLandSpec

ExecutorMode = Literal[
    "deterministic",
    "model_draft",
    "repository_agent_loop",
    "research_agent_loop",
    "interface_agent_loop",
    "validation",
    "composition",
]

EXECUTOR_MODES: frozenset[str] = frozenset(
    {
        "deterministic",
        "model_draft",
        "repository_agent_loop",
        "research_agent_loop",
        "interface_agent_loop",
        "validation",
        "composition",
    }
)

PACK_VALIDATOR_IDS: frozenset[str] = frozenset(
    {
        "acceptance_verification_links",
        "architecture_sections",
        "architecture_substance",
        "citation_presence",
        "document_sections",
        "feasibility_sections",
        "intake_no_invention",
        "intake_sections",
        "investigation_provenance",
        "investigation_sections",
        "no_invented_defaults",
        "option_comparison",
        "patch_applies",
        "path_scope",
        "quality_findings_sections",
        "regulated_claims_review",
        "research_provenance",
        "secret_scan",
        "security_evidence_sections",
        "spike_result_schema",
        "test_plan_sections",
        "verification_report_contract",
        "release_plan_contract",
        "deployment_record_contract",
        "operational_record_contract",
    }
)

# Derived from CapabilityDescriptor — packs never invent modes.
DEFAULT_EXECUTOR_MODES: dict[str, ExecutorMode] = {
    capability_id: descriptor.executor_mode  # type: ignore[misc]
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
}


@dataclass(frozen=True)
class PackExecutionPolicy:
    """Compiled, data-only runtime policy for a registered workflow pack."""

    executor_modes: dict[str, ExecutorMode]
    allowed_tool_classes: frozenset[str]
    validators: tuple[str, ...]
    output_roles: tuple[str, ...]
    denied_tool_names: frozenset[str] = frozenset()
    fallback_composition_roles: frozenset[str] = frozenset()
    required_output_roles: frozenset[str] = frozenset()
    exactly_one_output_role_groups: tuple[frozenset[str], ...] = ()
    accepted_handoff_schemas: frozenset[str] = frozenset()
    accepted_handoff_states: frozenset[str] = frozenset()
    accepted_handoff_roles: dict[str, frozenset[str]] = field(default_factory=dict)
    repair_eligible_capabilities: frozenset[str] = frozenset()
    findings_are_deliverable: bool = False
    approval_required: bool = False
    evaluation_fixture_id: str | None = None

    def validate(self, *, pack_id: str, capabilities: frozenset[str]) -> None:
        unknown_capabilities = set(self.executor_modes) - CAPABILITIES
        missing_capabilities = set(capabilities) - set(self.executor_modes)
        unknown_modes = set(self.executor_modes.values()) - EXECUTOR_MODES
        known_tool_classes = {
            tool_class for classes in CAPABILITY_TOOL_CLASSES.values() for tool_class in classes
        }
        unknown_tool_classes = set(self.allowed_tool_classes) - known_tool_classes
        permitted_union = frozenset(
            tool_class
            for capability in capabilities
            for tool_class in CAPABILITY_TOOL_CLASSES.get(capability, frozenset())
        )
        widened_tool_authority = sorted(set(self.allowed_tool_classes) - permitted_union)
        mode_mismatches: list[str] = []
        unknown_adapters: list[str] = []
        unknown_profiles: list[str] = []
        for capability in capabilities:
            if capability not in CAPABILITY_DESCRIPTORS:
                continue
            descriptor = require_descriptor(capability)
            declared_mode = self.executor_modes.get(capability)
            if declared_mode is not None and declared_mode != descriptor.executor_mode:
                mode_mismatches.append(f"{capability}:{declared_mode}!={descriptor.executor_mode}")
            if descriptor.executor_adapter_id not in KNOWN_EXECUTOR_ADAPTERS:
                unknown_adapters.append(f"{capability}:{descriptor.executor_adapter_id}")
            if descriptor.agent_profile_id not in KNOWN_AGENT_PROFILES:
                unknown_profiles.append(f"{capability}:{descriptor.agent_profile_id}")
        output_roles = set(self.output_roles)
        invalid_fallbacks = set(self.fallback_composition_roles) - output_roles
        invalid_required = set(self.required_output_roles) - output_roles
        invalid_groups = [
            sorted(group - output_roles)
            for group in self.exactly_one_output_role_groups
            if group - output_roles
        ]
        invalid_repairs = set(self.repair_eligible_capabilities) - set(capabilities)
        unknown_validators = set(self.validators) - PACK_VALIDATOR_IDS
        invalid_handoff_role_schemas = set(self.accepted_handoff_roles) - set(
            self.accepted_handoff_schemas
        )
        problems = {
            "unknown_capabilities": sorted(unknown_capabilities),
            "missing_capabilities": sorted(missing_capabilities),
            "unknown_executor_modes": sorted(unknown_modes),
            "unknown_tool_classes": sorted(unknown_tool_classes),
            "executor_mode_mismatches": sorted(mode_mismatches),
            "unknown_executor_adapters": sorted(unknown_adapters),
            "unknown_agent_profiles": sorted(unknown_profiles),
            "widened_tool_authority": widened_tool_authority,
            "invalid_fallback_roles": sorted(invalid_fallbacks),
            "invalid_required_roles": sorted(invalid_required),
            "invalid_role_groups": invalid_groups,
            "invalid_repair_capabilities": sorted(invalid_repairs),
            "unknown_validators": sorted(unknown_validators),
            "invalid_handoff_role_schemas": sorted(invalid_handoff_role_schemas),
        }
        failed = {key: value for key, value in problems.items() if value}
        if failed:
            raise ConfigurationError(f"Invalid PackExecutionPolicy for {pack_id!r}: {failed}")

    def executor_mode_for(self, capability: str) -> ExecutorMode:
        try:
            return self.executor_modes[capability]
        except KeyError as exc:
            raise ConfigurationError(
                f"No executor mode registered for capability {capability!r}"
            ) from exc

    def as_payload(self) -> dict[str, Any]:
        return {
            "executor_modes": dict(sorted(self.executor_modes.items())),
            "allowed_tool_classes": sorted(self.allowed_tool_classes),
            "validators": list(self.validators),
            "output_roles": list(self.output_roles),
            "denied_tool_names": sorted(self.denied_tool_names),
            "fallback_composition_roles": sorted(self.fallback_composition_roles),
            "required_output_roles": sorted(self.required_output_roles),
            "exactly_one_output_role_groups": [
                sorted(group) for group in self.exactly_one_output_role_groups
            ],
            "accepted_handoff_schemas": sorted(self.accepted_handoff_schemas),
            "accepted_handoff_states": sorted(self.accepted_handoff_states),
            "accepted_handoff_roles": {
                schema_id: sorted(roles)
                for schema_id, roles in sorted(self.accepted_handoff_roles.items())
            },
            "repair_eligible_capabilities": sorted(self.repair_eligible_capabilities),
            "findings_are_deliverable": self.findings_are_deliverable,
            "approval_required": self.approval_required,
            "evaluation_fixture_id": self.evaluation_fixture_id,
        }


def execution_policy(
    *,
    capabilities: frozenset[str],
    validators: list[str],
    output_roles: tuple[str, ...],
    allowed_tool_classes: frozenset[str] | None = None,
    **kwargs: Any,
) -> PackExecutionPolicy:
    """Build a policy with explicit executor modes for every pack capability."""

    modes: dict[str, ExecutorMode] = {
        capability: DEFAULT_EXECUTOR_MODES[capability]
        for capability in capabilities
        if capability in DEFAULT_EXECUTOR_MODES
    }
    tool_classes = allowed_tool_classes or frozenset(
        tool_class
        for capability in capabilities
        for tool_class in CAPABILITY_TOOL_CLASSES.get(capability, frozenset())
    )
    return PackExecutionPolicy(
        executor_modes=modes,
        allowed_tool_classes=tool_classes,
        validators=tuple(validators),
        output_roles=output_roles,
        **kwargs,
    )


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
    execution_policy: PackExecutionPolicy
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
            "execution_policy": self.execution_policy.as_payload(),
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
