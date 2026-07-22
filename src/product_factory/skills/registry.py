"""Skill registry and matching."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class SkillManifest(BaseModel):
    id: str
    version: str
    title: str
    capabilities: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=lambda: ["*"])
    frameworks: list[str] = Field(default_factory=lambda: ["*"])
    trigger: dict[str, Any] = Field(default_factory=dict)
    negative_triggers: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    prohibited_tools: list[str] = Field(default_factory=list)
    content_ref: str = "SKILL.md"
    status: str = "active"


class Skill(BaseModel):
    manifest: SkillManifest
    content: str
    path: Path


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None) -> None:
        self.skills = skills or []

    @classmethod
    def load(cls, root: Path) -> SkillRegistry:
        skills: list[Skill] = []
        if not root.exists():
            return cls(skills)
        for manifest_path in root.rglob("manifest.yaml"):
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            manifest = SkillManifest.model_validate(data)
            if manifest.status != "active":
                continue
            content_path = manifest_path.parent / manifest.content_ref
            content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
            skills.append(Skill(manifest=manifest, content=content, path=manifest_path.parent))
        return cls(skills)

    def match(
        self,
        *,
        capability: str,
        required_skills: list[str] | None = None,
        language: str | None = None,
    ) -> list[Skill]:
        required = set(required_skills or [])
        selected: list[Skill] = []
        for skill in self.skills:
            mid = skill.manifest.id
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
                    selected.append(skill)
        return selected
