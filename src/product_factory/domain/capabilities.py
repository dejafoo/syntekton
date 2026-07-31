"""Capability catalogue."""

from __future__ import annotations

from typing import Literal

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
]

CAPABILITIES: frozenset[str] = frozenset(
    {
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
    }
)

# Connector-backed tool classes. Listing one here only makes it *permissible*
# for a capability — a pack still has to request it, and an operator still has to
# enable the connector in `connectors.yaml`. Capabilities that write code are
# deliberately absent: an implementation task has no reason to read the web, and
# excluding it keeps untrusted external text out of patch-producing prompts.
EXTERNAL_READ_TOOL_CLASSES: frozenset[str] = frozenset({"web_read", "mcp_filesystem_read"})

# Tools permitted per capability (MVP defaults).
CAPABILITY_TOOL_CLASSES: dict[str, frozenset[str]] = {
    "requirements": frozenset({"repository_read"}),
    "architecture": frozenset({"repository_read", "artifact_write"}) | EXTERNAL_READ_TOOL_CLASSES,
    "repository_analysis": frozenset({"repository_read", "git_read"}) | EXTERNAL_READ_TOOL_CLASSES,
    "implementation": frozenset(
        {"repository_read", "repository_write", "git_read", "git_write", "validation_command"}
    ),
    "security_review": frozenset({"repository_read", "git_read"}) | EXTERNAL_READ_TOOL_CLASSES,
    "test_design": frozenset({"repository_read", "repository_write", "validation_command"})
    | EXTERNAL_READ_TOOL_CLASSES,
    "test_execution": frozenset({"repository_read", "validation_command"}),
    "documentation": frozenset({"repository_read", "artifact_write"}),
    "composition": frozenset({"repository_read", "artifact_write", "git_read"}),
    "independent_review": frozenset({"repository_read", "git_read"}),
    "repair": frozenset(
        {"repository_read", "repository_write", "git_read", "git_write", "validation_command"}
    ),
}
