"""Role admission criteria for local model routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProtocolCapability = Literal[
    "reachability",
    "model_identity",
    "structured_outputs",
    "tool_calling",
    "context_capacity",
    "latency",
]

# Task capabilities (scheduler roles) → required proven protocol features.
# Composition/planning stay cloud-first unless a local profile proves them.
ROLE_ADMISSION: dict[str, frozenset[ProtocolCapability]] = {
    "implementation": frozenset({"reachability", "model_identity", "tool_calling"}),
    "repair": frozenset({"reachability", "model_identity", "tool_calling"}),
    "independent_review": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "requirements": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "repository_analysis": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "security_review": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "testing": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "documentation": frozenset({"reachability", "model_identity", "structured_outputs"}),
    "planning": frozenset({"reachability", "model_identity", "structured_outputs", "tool_calling"}),
    "architecture": frozenset(
        {"reachability", "model_identity", "structured_outputs", "tool_calling"}
    ),
    "composition": frozenset(
        {"reachability", "model_identity", "structured_outputs", "tool_calling"}
    ),
    "difficult_reasoning": frozenset({"reachability", "model_identity", "structured_outputs"}),
}


@dataclass(frozen=True)
class AdmissionDecision:
    """Whether a local profile may serve a task capability."""

    admitted: bool
    role: str
    required: frozenset[str]
    proven: frozenset[str]
    missing: frozenset[str]
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "admitted": self.admitted,
            "role": self.role,
            "required": sorted(self.required),
            "proven": sorted(self.proven),
            "missing": sorted(self.missing),
            "reason": self.reason,
        }


def required_protocol_capabilities(
    *,
    task_capabilities: set[str] | frozenset[str],
) -> frozenset[str]:
    """Union of protocol proofs required by the profile's task capabilities."""
    required: set[str] = {"reachability", "model_identity"}
    for capability in task_capabilities:
        required.update(ROLE_ADMISSION.get(capability, frozenset()))
    return frozenset(required)


def evaluate_admission(
    *,
    task_capabilities: set[str] | frozenset[str],
    proven: set[str] | frozenset[str],
    primary_role: str | None = None,
) -> AdmissionDecision:
    """Admit only when every required protocol capability is proven."""
    role = primary_role or next(iter(sorted(task_capabilities)), "unknown")
    required = required_protocol_capabilities(task_capabilities=task_capabilities)
    if primary_role and primary_role in ROLE_ADMISSION:
        required = frozenset(ROLE_ADMISSION[primary_role]) | frozenset(
            {"reachability", "model_identity"}
        )
    missing = frozenset(required - set(proven))
    return AdmissionDecision(
        admitted=not missing,
        role=role,
        required=required,
        proven=frozenset(proven),
        missing=missing,
        reason="capability_miss" if missing else None,
    )
