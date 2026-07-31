"""Skill package manifest contract (PM0.C / G0)."""

from __future__ import annotations

import hashlib
from typing import Any

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
    # PM0.C additive fields (defaults preserve current packs).
    owner: str = "product-factory"
    package_digest: str | None = None
    input_schema_id: str | None = None
    output_schema_id: str | None = None
    profile_slots: list[str] = Field(default_factory=list)
    max_prompt_chars: int | None = None
    deprecated_by: str | None = None


def compute_package_digest(*, manifest_yaml: str, skill_md: str) -> str:
    payload = (manifest_yaml.strip() + "\n---\n" + skill_md.strip()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
