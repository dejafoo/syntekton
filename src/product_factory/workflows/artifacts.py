"""Artifact land map — deliverable role, produced name, and suggested destination.

A workflow pack declares *roles* (stable keys such as ``architecture_document``)
rather than filenames. Each role carries a default produced name and a default
destination under the target repository. Hosts may override either per run, so a
``technical_plan`` can deliver ``integration_testing_architecture.md`` instead of
the generic ``ARCHITECTURE.md`` without forking the pack or the client.

Validators key off document *content* (headings, substance), never the basename,
so renaming a deliverable never changes whether it passes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

ROLE_ARCHITECTURE_DOCUMENT = "architecture_document"
ROLE_EVIDENCE_REPORT = "evidence_report"
ROLE_PROPOSED_PATCH = "proposed_patch"


@dataclass(frozen=True)
class ArtifactLandSpec:
    """Pack-declared deliverable: a role plus its default names."""

    role: str
    default_logical_name: str
    default_dest_path: str
    media_type: str = "text/markdown"
    # Patches are applied through `approve --apply`, never copied by materialize.
    landable: bool = True
    # Names other subsystems read back by path (e.g. `proposed.patch`) are fixed.
    renamable: bool = True
    required: bool = True
    description: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "default_logical_name": self.default_logical_name,
            "default_dest_path": self.default_dest_path,
            "media_type": self.media_type,
            "landable": self.landable,
            "renamable": self.renamable,
            "required": self.required,
        }


@dataclass(frozen=True)
class ResolvedArtifact:
    """A land-map entry after applying overrides."""

    role: str
    logical_name: str
    dest_path: str
    media_type: str = "text/markdown"
    landable: bool = True
    renamable: bool = True
    required: bool = True
    default_logical_name: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "logical_name": self.logical_name,
            "suggested_dest_path": self.dest_path,
            "media_type": self.media_type,
            "landable": self.landable,
            "renamable": self.renamable,
            "required": self.required,
        }


@dataclass(frozen=True)
class ArtifactLandMap:
    """Resolved deliverables for one run."""

    entries: tuple[ResolvedArtifact, ...] = ()

    def by_role(self, role: str) -> ResolvedArtifact | None:
        for entry in self.entries:
            if entry.role == role:
                return entry
        return None

    def logical_name_for(self, role: str, *, default: str) -> str:
        entry = self.by_role(role)
        return entry.logical_name if entry is not None else default

    def dest_path_for(self, role: str, *, default: str | None = None) -> str | None:
        entry = self.by_role(role)
        return entry.dest_path if entry is not None else default

    def landable(self) -> tuple[ResolvedArtifact, ...]:
        return tuple(entry for entry in self.entries if entry.landable)

    def role_for_logical_name(self, logical_name: str) -> str | None:
        """Map a produced artifact back to its role.

        Accepts the resolved name and the pack default, so runs recorded before
        an override (or with a different override) still classify correctly.
        """
        name = (logical_name or "").strip().lower()
        if not name:
            return None
        for entry in self.entries:
            if name == entry.logical_name.lower():
                return entry.role
            if entry.default_logical_name and name == entry.default_logical_name.lower():
                return entry.role
        return None

    def as_payload(self) -> list[dict[str, Any]]:
        return [entry.as_payload() for entry in self.entries]


class ArtifactOverrideError(ValueError):
    """An override names an unknown role or an unsafe destination."""


def _validate_dest_path(role: str, dest_path: str) -> str:
    raw = (dest_path or "").strip()
    if not raw:
        raise ArtifactOverrideError(f"Empty dest_path for role {role!r}")
    if raw.startswith("~"):
        raise ArtifactOverrideError(
            f"dest_path for {role!r} must be repository-relative, got {raw!r}"
        )
    normalized = raw.replace("\\", "/")
    if normalized.endswith("/"):
        raise ArtifactOverrideError(
            f"dest_path for {role!r} must name a file, not a directory: {raw!r}"
        )
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ArtifactOverrideError(
            f"dest_path for {role!r} must be repository-relative, got {raw!r}"
        )
    if any(part == ".." for part in path.parts):
        raise ArtifactOverrideError(
            f"dest_path for {role!r} must not traverse outside the repository: {raw!r}"
        )
    if not path.name:
        raise ArtifactOverrideError(f"dest_path for {role!r} has no filename: {raw!r}")
    return str(path)


def _validate_logical_name(role: str, logical_name: str) -> str:
    raw = (logical_name or "").strip()
    if not raw:
        raise ArtifactOverrideError(f"Empty logical_name for role {role!r}")
    path = PurePosixPath(raw.replace("\\", "/"))
    if len(path.parts) != 1 or path.name != raw:
        raise ArtifactOverrideError(
            f"logical_name for {role!r} must be a bare filename, got {raw!r}"
        )
    return raw


def normalize_overrides(raw: Any) -> dict[str, dict[str, str]]:
    """Accept the several shapes hosts send and return ``{role: {...}}``.

    Supported inputs:

    - ``{"architecture_document": {"dest_path": "docs/x.md"}}``
    - ``{"architecture_document": "docs/x.md"}`` (destination shorthand)
    - ``["architecture_document=docs/x.md"]`` (CLI / ``requested_artifacts``)
    - a JSON string of any of the above (metadata passthrough)
    """
    if raw is None:
        return {}
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            return normalize_overrides(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ArtifactOverrideError(
                f"artifact_overrides is not valid JSON: {exc.msg}"
            ) from None
    out: dict[str, dict[str, str]] = {}
    if isinstance(raw, dict):
        for role, value in raw.items():
            key = str(role).strip()
            if not key:
                continue
            if isinstance(value, str):
                out[key] = {"dest_path": value.strip()}
            elif isinstance(value, dict):
                spec: dict[str, str] = {}
                for field in ("logical_name", "dest_path"):
                    if value.get(field):
                        spec[field] = str(value[field]).strip()
                if spec:
                    out[key] = spec
            elif value is not None:
                raise ArtifactOverrideError(
                    f"Unsupported override for role {key!r}: {type(value).__name__}"
                )
        return out
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, str) or "=" not in item:
                raise ArtifactOverrideError(f"Expected ROLE=DEST_PATH entries, got {item!r}")
            role, _, dest = item.partition("=")
            role = role.strip()
            dest = dest.strip()
            if role and dest:
                out[role] = {"dest_path": dest}
        return out
    raise ArtifactOverrideError(f"Unsupported artifact_overrides type: {type(raw).__name__}")


def resolve_artifact_land_map(
    specs: tuple[ArtifactLandSpec, ...] | list[ArtifactLandSpec],
    *,
    overrides: Any = None,
    planner_artifacts: Any = None,
) -> ArtifactLandMap:
    """Resolve pack defaults against planner output and host overrides.

    Precedence (first wins): host override, planner-declared artifact, pack
    default. Unknown override roles fail closed so a typo never silently lands
    the generic default.
    """
    resolved: list[ResolvedArtifact] = []
    for spec in specs:
        resolved.append(
            ResolvedArtifact(
                role=spec.role,
                logical_name=spec.default_logical_name,
                dest_path=spec.default_dest_path,
                media_type=spec.media_type,
                landable=spec.landable,
                renamable=spec.renamable,
                required=spec.required,
                default_logical_name=spec.default_logical_name,
            )
        )
    index = {entry.role: position for position, entry in enumerate(resolved)}

    for artifact in planner_artifacts or []:
        role = getattr(artifact, "role", None)
        logical = getattr(artifact, "logical_name", None)
        dest = getattr(artifact, "dest_path", None)
        if not role or role not in index:
            continue
        position = index[role]
        current = resolved[position]
        updates: dict[str, str] = {}
        if logical:
            updates["logical_name"] = _validate_logical_name(role, str(logical))
        if dest:
            dest_value = _validate_dest_path(role, str(dest))
            updates["dest_path"] = dest_value
            if not logical:
                updates["logical_name"] = PurePosixPath(dest_value).name
        if updates:
            resolved[position] = replace(current, **updates)

    for role, spec_overrides in normalize_overrides(overrides).items():
        if role not in index:
            raise ArtifactOverrideError(
                f"Unknown artifact role {role!r}; known roles: {sorted(index)}"
            )
        position = index[role]
        current = resolved[position]
        if not current.renamable:
            raise ArtifactOverrideError(
                f"Artifact role {role!r} has a fixed name ({current.logical_name}) "
                "and cannot be renamed"
            )
        updates = {}
        dest = spec_overrides.get("dest_path")
        logical = spec_overrides.get("logical_name")
        if dest:
            dest_value = _validate_dest_path(role, dest)
            updates["dest_path"] = dest_value
            # A host that only picks a destination gets a matching produced name,
            # so the run store and the landed file agree.
            if not logical:
                updates["logical_name"] = PurePosixPath(dest_value).name
        if logical:
            updates["logical_name"] = _validate_logical_name(role, logical)
            if not dest:
                parent = PurePosixPath(current.dest_path).parent
                updates["dest_path"] = str(parent / updates["logical_name"])
        if updates:
            resolved[position] = replace(current, **updates)

    return ArtifactLandMap(entries=tuple(resolved))


def document_title_for(logical_name: str) -> str:
    """H1 title for a composed markdown deliverable."""
    name = (logical_name or "").strip()
    return name or "ARCHITECTURE.md"
