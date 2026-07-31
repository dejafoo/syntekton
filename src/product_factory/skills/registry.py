"""Skill registry and matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from product_factory.skills.manifest import SkillManifest, compute_package_digest

__all__ = ["Skill", "SkillManifest", "SkillRegistry", "compute_package_digest"]


class Skill(BaseModel):
    manifest: SkillManifest
    content: str
    path: Path
    package_digest: str = ""


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None) -> None:
        self.skills = skills or []

    @classmethod
    def load(cls, root: Path) -> SkillRegistry:
        skills: list[Skill] = []
        if not root.exists():
            return cls(skills)
        for manifest_path in root.rglob("manifest.yaml"):
            raw = manifest_path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) or {}
            manifest = SkillManifest.model_validate(data)
            if manifest.status != "active":
                continue
            content_path = manifest_path.parent / manifest.content_ref
            content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
            digest = manifest.package_digest or compute_package_digest(
                manifest_yaml=raw, skill_md=content
            )
            skills.append(
                Skill(
                    manifest=manifest,
                    content=content,
                    path=manifest_path.parent,
                    package_digest=digest,
                )
            )
        return cls(skills)

    def get(self, skill_id: str) -> Skill | None:
        for skill in self.skills:
            if skill.manifest.id == skill_id or skill.manifest.title == skill_id:
                return skill
        return None

    def match(
        self,
        *,
        capability: str,
        required_skills: list[str] | None = None,
        language: str | None = None,
        skill_policy: dict[str, Any] | None = None,
    ) -> list[Skill]:
        required = set(required_skills or [])
        policy = skill_policy or {}
        allow = set(policy.get("allow") or policy.get("allowlist") or [])
        deny = set(policy.get("deny") or policy.get("denylist") or [])
        selected: list[Skill] = []
        for skill in self.skills:
            mid = skill.manifest.id
            if mid in deny or skill.manifest.title in deny:
                continue
            if allow and mid not in allow and skill.manifest.title not in allow:
                continue
            if required and mid not in required and skill.manifest.title not in required:
                # If explicit requirements exist, prefer those; also allow capability match.
                if capability not in skill.manifest.capabilities:
                    continue
            elif capability not in skill.manifest.capabilities and mid not in required:
                continue
            if (
                language
                and language not in skill.manifest.languages
                and "*" not in skill.manifest.languages
            ):
                continue
            selected.append(skill)
        # Always include explicitly required
        if required:
            for skill in self.skills:
                if skill.manifest.id in required and skill not in selected:
                    if skill.manifest.id in deny:
                        continue
                    selected.append(skill)
        return selected
