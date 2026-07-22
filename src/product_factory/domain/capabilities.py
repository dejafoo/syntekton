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

# Tools permitted per capability (MVP defaults).
CAPABILITY_TOOL_CLASSES: dict[str, frozenset[str]] = {
    "requirements": frozenset({"repository_read"}),
    "architecture": frozenset({"repository_read", "artifact_write"}),
    "repository_analysis": frozenset({"repository_read", "git_read"}),
    "implementation": frozenset(
        {"repository_read", "repository_write", "git_read", "git_write", "validation_command"}
    ),
    "security_review": frozenset({"repository_read", "git_read"}),
    "test_design": frozenset({"repository_read", "repository_write", "validation_command"}),
    "test_execution": frozenset({"repository_read", "validation_command"}),
    "documentation": frozenset({"repository_read", "artifact_write"}),
    "composition": frozenset({"repository_read", "artifact_write", "git_read"}),
    "independent_review": frozenset({"repository_read", "git_read"}),
    "repair": frozenset(
        {"repository_read", "repository_write", "git_read", "git_write", "validation_command"}
    ),
}
