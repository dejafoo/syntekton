"""Stack profile persistence under ``profiles/stack``."""

from __future__ import annotations

from pathlib import Path

import yaml

from product_factory.domain.errors import ConfigurationError
from product_factory.repository.stack_profile import StackProfile


class ProfileRegistry:
    """Deterministic stack profiles discovered under ``<root>/stack/*.yaml``."""

    def __init__(self, profiles: list[StackProfile] | None = None) -> None:
        self.profiles = sorted(profiles or [], key=lambda profile: profile.id)

    @classmethod
    def load(cls, root: Path) -> ProfileRegistry:
        root = Path(root)
        stack_root = root if root.name == "stack" else root / "stack"
        profiles: list[StackProfile] = []
        if not stack_root.is_dir():
            return cls(profiles)
        for path in sorted(stack_root.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Stack profile must be a mapping: {path}",
                    details={"path": str(path)},
                )
            data.setdefault("id", path.stem)
            profiles.append(StackProfile.model_validate(data))
        return cls(profiles)

    def get(self, profile_id: str) -> StackProfile | None:
        return next((profile for profile in self.profiles if profile.id == profile_id), None)

    def store(self, root: Path, profile: StackProfile) -> Path:
        root = Path(root)
        stack_root = root if root.name == "stack" else root / "stack"
        stack_root.mkdir(parents=True, exist_ok=True)
        path = stack_root / f"{profile.id}.yaml"
        path.write_text(
            yaml.safe_dump(
                profile.model_dump(mode="json"),
                sort_keys=True,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        if self.get(profile.id) is None:
            self.profiles.append(profile)
        else:
            self.profiles = [
                profile if existing.id == profile.id else existing
                for existing in self.profiles
            ]
        self.profiles.sort(key=lambda item: item.id)
        return path

    def digests(self) -> dict[str, str]:
        return {
            key: digest
            for profile in self.profiles
            for key, digest in profile.as_manifest_entry().items()
        }
