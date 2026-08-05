"""Composition policy profiles for regulated discovery and deployment (PM5.C)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError

POLICY_PROFILE_DIRNAME = "policy"
DIGEST_KEY_PREFIX = "composition_policy:"


class CompositionPolicyProfile(BaseModel):
    """Declarative composition controls; never widens tool or approval authority."""

    model_config = {"extra": "forbid"}

    id: str
    version: str = "0.0.0"
    kind: str = "composition_policy"
    description: str = ""
    require_human_review_for: list[str] = Field(default_factory=list)
    deny_authority_widening: bool = True
    deny_additional_tool_classes: bool = True
    forbid_live_sensitive_systems: bool = True
    allowed_data_classifications: list[str] = Field(default_factory=list)
    prohibited_conclusions: list[str] = Field(default_factory=list)
    permitted_workflows: list[str] = Field(default_factory=list)
    require_approval_for_effect: bool = False
    provider_target_profiles_require_broker: bool = False

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_manifest_entry(self) -> dict[str, str]:
        return {f"{DIGEST_KEY_PREFIX}{self.id}": self.digest}

    def requires_human_review(self, topic: str) -> bool:
        needle = (topic or "").strip().lower()
        return needle in {t.strip().lower() for t in self.require_human_review_for}


class PolicyProfileRegistry:
    """Profiles discovered under ``<profiles_root>/policy/*.yaml``."""

    def __init__(self, profiles: list[CompositionPolicyProfile] | None = None) -> None:
        self._profiles: dict[str, CompositionPolicyProfile] = {p.id: p for p in profiles or []}

    @classmethod
    def load(cls, profiles_root: Path) -> PolicyProfileRegistry:
        root = Path(profiles_root) / POLICY_PROFILE_DIRNAME
        if not root.is_dir():
            return cls([])
        profiles: list[CompositionPolicyProfile] = []
        for path in sorted(root.glob("*.yaml")):
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Composition policy profile must be a mapping: {path}",
                    details={"path": str(path)},
                )
            data.setdefault("id", path.stem)
            profiles.append(CompositionPolicyProfile.model_validate(data))
        return cls(profiles)

    def get(self, profile_id: str) -> CompositionPolicyProfile | None:
        return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> CompositionPolicyProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise ConfigurationError(
                f"Unknown composition policy profile: {profile_id!r}",
                details={"profile_id": profile_id, "known": self.ids()},
            )
        return profile

    def ids(self) -> list[str]:
        return sorted(self._profiles)

    def digests(self) -> dict[str, str]:
        return {
            f"{DIGEST_KEY_PREFIX}{profile.id}": profile.digest
            for profile in self._profiles.values()
        }


def resolve_policy_profile(
    profile_id: str,
    *,
    profiles_root: Path,
) -> CompositionPolicyProfile:
    return PolicyProfileRegistry.load(profiles_root).require(profile_id)


def resolve_request_policy_profiles(
    request: Any,
    *,
    profiles_root: Path,
) -> list[CompositionPolicyProfile]:
    """Resolve composition policy profiles from pack_input / workflow defaults."""

    pack_input = getattr(request, "pack_input", None) or {}
    raw = pack_input.get("composition_policy_profile") or pack_input.get(
        "composition_policy_profiles"
    )
    workflow = str(getattr(request, "workflow_type", "") or "")
    ids: list[str] = []
    if isinstance(raw, str) and raw.strip():
        ids = [raw.strip()]
    elif isinstance(raw, list):
        ids = [str(item).strip() for item in raw if str(item).strip()]
    elif raw:
        raise ConfigurationError(
            "composition_policy_profile must be a string or list of strings",
            details={"value_type": type(raw).__name__},
        )
    elif workflow == "feasibility_discovery" and pack_input.get("domain_reference_pack"):
        ids = ["regulated-data"]
    elif workflow == "deployment_execution":
        ids = ["deployment-composition"]
    if not ids:
        return []
    registry = PolicyProfileRegistry.load(profiles_root)
    return [registry.require(profile_id) for profile_id in ids]


__all__ = [
    "DIGEST_KEY_PREFIX",
    "POLICY_PROFILE_DIRNAME",
    "CompositionPolicyProfile",
    "PolicyProfileRegistry",
    "resolve_policy_profile",
    "resolve_request_policy_profiles",
]
