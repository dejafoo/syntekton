"""Source policy profiles — which external evidence a run may rely on (PM1.0).

A profile is declarative data loaded from `profiles/source/*.yaml`, following
the PM0 profile-stub pattern. It bounds the *class* and *freshness* of evidence
a discovery run may cite and names the topics that require a human expert
verdict; it never widens tool or approval authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError

SourceClass = Literal[
    "standard",
    "regulator",
    "vendor_api",
    "operator_artifact",
    "secondary_commentary",
    "unknown",
]

SOURCE_CLASSES: tuple[SourceClass, ...] = (
    "standard",
    "regulator",
    "vendor_api",
    "operator_artifact",
    "secondary_commentary",
    "unknown",
)

DEFAULT_SOURCE_POLICY_PROFILE_ID = "public-technical"
SOURCE_PROFILE_DIRNAME = "source"
# Prefix keeps source-policy digests distinct from stack-profile digests in the
# shared `profile_digests` map on the task-context manifest.
DIGEST_KEY_PREFIX = "source_policy:"


class SourcePolicyProfile(BaseModel):
    """One resolved source policy; `digest` pins the content that was applied."""

    model_config = {"extra": "forbid"}

    id: str
    version: str = "0.0.0"
    description: str = ""
    allowed_source_classes: list[SourceClass] = Field(default_factory=list)
    preferred_source_classes: list[SourceClass] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    max_source_age_days: int | None = None
    require_expert_review_for: list[str] = Field(default_factory=list)

    @property
    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def allows_source_class(self, source_class: str) -> bool:
        return source_class in self.allowed_source_classes

    def allows_domain(self, host: str) -> bool:
        """Deny list wins; an empty allow list means any non-denied host."""
        name = (host or "").strip().lower().rstrip(".")
        if not name:
            return False
        if any(_host_matches(name, entry) for entry in self.denied_domains):
            return False
        if not self.allowed_domains:
            return True
        return any(_host_matches(name, entry) for entry in self.allowed_domains)

    def requires_expert_review(self, topic: str) -> bool:
        return (topic or "").strip().lower() in {
            t.strip().lower() for t in self.require_expert_review_for
        }

    def is_stale(self, published_at: datetime | None, *, now: datetime | None = None) -> bool:
        """True when a source is older than the profile allows.

        An unknown publication date is stale whenever the profile sets an age
        cap: freshness that cannot be shown has not been shown.
        """
        if self.max_source_age_days is None:
            return False
        if published_at is None:
            return True
        moment = now or datetime.now(UTC)
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        return (moment - published_at).days > self.max_source_age_days

    def as_manifest_entry(self) -> dict[str, str]:
        return {f"{DIGEST_KEY_PREFIX}{self.id}": self.digest}


def _host_matches(host: str, entry: str) -> bool:
    suffix = (entry or "").strip().lower().lstrip("*.").rstrip(".")
    if not suffix:
        return False
    return host == suffix or host.endswith(f".{suffix}")


class SourcePolicyRegistry:
    """Profiles discovered under `<profiles_root>/source/*.yaml`."""

    def __init__(self, profiles: list[SourcePolicyProfile] | None = None) -> None:
        self._profiles: dict[str, SourcePolicyProfile] = {p.id: p for p in profiles or []}

    @classmethod
    def load(cls, profiles_root: Path) -> SourcePolicyRegistry:
        root = Path(profiles_root) / SOURCE_PROFILE_DIRNAME
        if not root.is_dir():
            return cls([])
        profiles: list[SourcePolicyProfile] = []
        for path in sorted(root.glob("*.yaml")):
            data: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Source policy profile must be a mapping: {path}",
                    details={"path": str(path)},
                )
            data.setdefault("id", path.stem)
            profiles.append(SourcePolicyProfile.model_validate(data))
        return cls(profiles)

    def get(self, profile_id: str) -> SourcePolicyProfile | None:
        return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> SourcePolicyProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise ConfigurationError(
                f"Unknown source policy profile: {profile_id!r}",
                details={"profile_id": profile_id, "known": self.ids()},
            )
        return profile

    def ids(self) -> list[str]:
        return sorted(self._profiles)

    def digests(self) -> dict[str, str]:
        return {f"{DIGEST_KEY_PREFIX}{p.id}": p.digest for p in self._profiles.values()}


def resolve_source_policy(
    profile_id: str,
    *,
    profiles_root: Path,
) -> SourcePolicyProfile:
    """Resolve one profile by id, failing closed when it is not shipped."""
    return SourcePolicyRegistry.load(profiles_root).require(profile_id)


def resolve_request_source_policy(
    request: Any,
    *,
    profiles_root: Path,
) -> SourcePolicyProfile | None:
    """Resolve the profile a request's typed pack input selects, if any.

    Discovery defaults to `public-technical` when the pack input omits a profile
    so evidence tools always have a bound policy. Other packs stay unaffected
    and return `None` when no profile is named.
    """
    pack_input = getattr(request, "pack_input", None) or {}
    profile_id = str(pack_input.get("source_policy_profile") or "").strip()
    if not profile_id:
        workflow = str(getattr(request, "workflow_type", "") or "")
        if workflow == "feasibility_discovery":
            profile_id = DEFAULT_SOURCE_POLICY_PROFILE_ID
        else:
            return None
    return resolve_source_policy(profile_id, profiles_root=profiles_root)
