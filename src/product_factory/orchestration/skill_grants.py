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

from collections.abc import Mapping

from product_factory.domain.errors import SkillGrantViolation
from product_factory.skills.registry import Skill

# Maps a skill-declared tool name to the set of concrete broker tool names it
# is satisfied by / denies. Tool-class entries expand to every broker tool in
# that class; concrete tool names map to themselves.
SKILL_TOOL_GRANT_MAP: dict[str, frozenset[str]] = {
    "repository_read": frozenset({"read_file", "list_files", "search_text"}),
    "repository_write": frozenset({"create_file", "apply_patch"}),
    "git_read": frozenset({"git_diff", "git_status"}),
    "git_write": frozenset({"apply_patch"}),
    "artifact_write": frozenset({"write_artifact"}),
    "validation_command": frozenset({"run_validation_command"}),
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

# Umbrella name for "reaches outside this machine's repository". A skill that
# prohibits it must deny every connector, including ones added later, so this
# resolves against the live connector registry rather than a hardcoded list.
NETWORK_ACCESS = "network_access"


def resolve_broker_tool_names(
    skill_tool_name: str,
    *,
    connector_tools: Mapping[str, frozenset[str]] | None = None,
) -> frozenset[str]:
    """Return the concrete broker tool names a skill-declared name maps to.

    `connector_tools` maps a connector tool class to its tool names. Unknown
    names resolve to the empty set (fail closed for `required_tools`, never a
    false-positive violation for `prohibited_tools`).
    """
    builtin = SKILL_TOOL_GRANT_MAP.get(skill_tool_name)
    if builtin is not None:
        return builtin
    by_class = connector_tools or {}
    if skill_tool_name == NETWORK_ACCESS:
        return frozenset().union(*by_class.values()) if by_class else frozenset()
    if skill_tool_name in by_class:
        return by_class[skill_tool_name]
    for names in by_class.values():
        if skill_tool_name in names:
            return frozenset({skill_tool_name})
    return frozenset()


def enforce_skill_grants(
    *,
    skills: list[Skill],
    granted_tool_names: set[str],
    connector_tools: Mapping[str, frozenset[str]] | None = None,
) -> None:
    """Fail closed when a matched skill's tool policy is inconsistent with the grant.

    - Every `required_tools` entry must resolve to at least one grantable
      broker tool name that is actually present in `granted_tool_names`.
    - No `prohibited_tools` entry may resolve to a broker tool name present
      in `granted_tool_names`.
    """
    for skill in skills:
        manifest = skill.manifest
        for required in manifest.required_tools:
            allowed = resolve_broker_tool_names(required, connector_tools=connector_tools)
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
            denied = resolve_broker_tool_names(prohibited, connector_tools=connector_tools)
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
