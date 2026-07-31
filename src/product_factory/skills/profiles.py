"""Stack/policy profile stubs (PM0.C forward-compat)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProfileManifest(BaseModel):
    id: str
    version: str = "0.0.0"
    kind: str = "stack"
    description: str = ""
    slots: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProfileRegistry:
    """Empty/minimal profile registry — PM1/PM4 fill real profiles."""

    def __init__(self, profiles: list[ProfileManifest] | None = None) -> None:
        self.profiles = profiles or []

    @classmethod
    def load(cls, root: Path) -> ProfileRegistry:
        profiles: list[ProfileManifest] = []
        if not root.exists():
            return cls(profiles)
        for path in root.rglob("profile.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            profiles.append(ProfileManifest.model_validate(data))
        return cls(profiles)

    def digests(self) -> dict[str, str]:
        # Stubs have no content digests yet.
        return {p.id: f"{p.id}@{p.version}" for p in self.profiles}
