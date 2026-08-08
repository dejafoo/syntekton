"""Capability catalogue — generated views over CapabilityDescriptor (SD1.A)."""

from __future__ import annotations

from typing import Literal

from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    permissible_tool_classes_for,
)
from product_factory.registry.capability_descriptors import (
    DEPLOYMENT_TOOL_CLASSES as _DEPLOYMENT_TOOL_CLASSES,
)
from product_factory.registry.capability_descriptors import (
    EXTERNAL_READ_TOOL_CLASSES as _EXTERNAL_READ_TOOL_CLASSES,
)

Capability = Literal[
    "requirements",
    "architecture",
    "repository_analysis",
    "implementation",
    "security_review",
    "test_design",
    "test_execution",
    "documentation",
    "composition",
    "independent_review",
    "repair",
    "domain_research",
    "decision_analysis",
    "interface_analysis",
    "release_analysis",
    "operations_analysis",
    "deployment_execution",
]

CAPABILITIES: frozenset[str] = frozenset(CAPABILITY_DESCRIPTORS)

# Connector-backed tool classes. Listing one here only makes it *permissible*
# for a capability — a pack still has to request it, and an operator still has to
# enable the connector in `connectors.yaml`. Capabilities that write code are
# deliberately absent: an implementation task has no reason to read the web, and
# excluding it keeps untrusted external text out of patch-producing prompts.
# `source_read` (URL retrieval) and `evidence_build` (local extraction, citation,
# comparison) are deliberately absent from most defaults: they reach a task only
# through an explicit `required_tool_classes` declaration, so existing
# architecture/repository_analysis/security_review tasks do not silently gain
# retrieval when a discovery pack ships.
EXTERNAL_READ_TOOL_CLASSES: frozenset[str] = _EXTERNAL_READ_TOOL_CLASSES

# Deployment connectors are deliberately isolated from every analysis
# capability. A pack must opt into ``deployment_execution`` and explicitly
# request one of these classes before an operator-enabled connector is
# grantable.
DEPLOYMENT_TOOL_CLASSES: frozenset[str] = _DEPLOYMENT_TOOL_CLASSES

# Tools permitted per capability — derived from the descriptor registry.
CAPABILITY_TOOL_CLASSES: dict[str, frozenset[str]] = {
    capability_id: descriptor.permissible_tool_classes
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
}


def tool_classes_for(capability: str) -> frozenset[str]:
    return permissible_tool_classes_for(capability)
