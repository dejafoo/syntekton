"""Explicit skill-tool-name to broker-tool-name mapping and grant-time enforcement.

Skill manifests declare `required_tools` / `prohibited_tools` using either a
tool_class name (e.g. ``repository_read``) or a concrete broker tool name
(e.g. ``git_diff``). This module is the single source of truth translating
those skill-facing names into the concrete `tools/broker.py` tool names they
grant or deny, and enforces consistency at grant time (P1.E): required tools
must be satisfiable by the task's actual grant, and prohibited tools must
never be present in it. Violations fail closed (raise), never silently
downgrade.
"""

from __future__ import annotations

from product_factory.domain.errors import SkillGrantViolation
from product_factory.skills.registry import Skill

# Maps a skill-declared tool name to the set of concrete broker tool names it
# is satisfied by / denies. Tool-class entries expand to every broker tool in
# that class; concrete tool names map to themselves. `network_access` is
# never grantable in Phase 1 (no MCP/web access), so it always maps to the
# empty set — any skill requiring it fails closed, and any skill prohibiting
# it can never be violated by the current tool surface.
SKILL_TOOL_GRANT_MAP: dict[str, frozenset[str]] = {
    "repository_read": frozenset({"read_file", "list_files", "search_text"}),
    "repository_write": frozenset({"create_file", "apply_patch"}),
    "git_read": frozenset({"git_diff", "git_status"}),
    "git_write": frozenset({"apply_patch"}),
    "artifact_write": frozenset({"write_artifact"}),
    "validation_command": frozenset({"run_validation_command"}),
    "network_access": frozenset(),
    # Concrete broker tool names pass through unchanged.
    "read_file": frozenset({"read_file"}),
    "list_files": frozenset({"list_files"}),
    "search_text": frozenset({"search_text"}),
    "git_diff": frozenset({"git_diff"}),
    "git_status": frozenset({"git_status"}),
    "apply_patch": frozenset({"apply_patch"}),
    "create_file": frozenset({"create_file"}),
    "write_artifact": frozenset({"write_artifact"}),
    "run_validation_command": frozenset({"run_validation_command"}),
}


def resolve_broker_tool_names(skill_tool_name: str) -> frozenset[str]:
    """Return the concrete broker tool names a skill-declared name maps to.

    Unknown names resolve to the empty set (fail closed for `required_tools`,
    never a false-positive violation for `prohibited_tools`).
    """
    return SKILL_TOOL_GRANT_MAP.get(skill_tool_name, frozenset())


def enforce_skill_grants(*, skills: list[Skill], granted_tool_names: set[str]) -> None:
    """Fail closed when a matched skill's tool policy is inconsistent with the grant.

    - Every `required_tools` entry must resolve to at least one grantable
      broker tool name that is actually present in `granted_tool_names`.
    - No `prohibited_tools` entry may resolve to a broker tool name present
      in `granted_tool_names`.
    """
    for skill in skills:
        manifest = skill.manifest
        for required in manifest.required_tools:
            allowed = resolve_broker_tool_names(required)
            if not allowed or not (allowed & granted_tool_names):
                raise SkillGrantViolation(
                    f"Skill {manifest.id!r} requires tool {required!r} but it is not "
                    "grantable/granted for this task",
                    details={
                        "skill_id": manifest.id,
                        "required_tool": required,
                        "resolved_broker_tools": sorted(allowed),
                        "granted_tool_names": sorted(granted_tool_names),
                    },
                )
        for prohibited in manifest.prohibited_tools:
            denied = resolve_broker_tool_names(prohibited)
            overlap = denied & granted_tool_names
            if overlap:
                raise SkillGrantViolation(
                    f"Skill {manifest.id!r} prohibits {prohibited!r} but the grant "
                    f"includes {sorted(overlap)}",
                    details={
                        "skill_id": manifest.id,
                        "prohibited_tool": prohibited,
                        "overlap": sorted(overlap),
                    },
                )
