"""Resolved task-context manifest persisted before model dispatch (PM0.C)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from product_factory.domain.errors import BudgetExhaustedError
from product_factory.skills.registry import Skill


class TaskContextManifest(BaseModel):
    task_id: str
    primary_skill_id: str | None = None
    primary_skill_digest: str | None = None
    skill_digests: dict[str, str] = Field(default_factory=dict)
    specialization: str | None = None
    profile_digests: dict[str, str] = Field(default_factory=dict)
    tool_grant_set: list[str] = Field(default_factory=list)
    prompt_tool_names: list[str] = Field(default_factory=list)
    effective_policy: dict[str, Any] | None = None
    schema_expectations: dict[str, str] = Field(default_factory=dict)
    estimated_prompt_chars: int = 0
    max_prompt_chars: int | None = None


def skill_bundle_chars(skills: list[Skill]) -> int:
    return sum(len(s.content) + len(s.manifest.id) + 32 for s in skills)


def resolve_skill_budget(skills: list[Skill], *, policy_ceiling: int | None = None) -> int | None:
    caps = [s.manifest.max_prompt_chars for s in skills if s.manifest.max_prompt_chars]
    if policy_ceiling is not None:
        caps.append(policy_ceiling)
    if not caps:
        return None
    return min(caps)


def build_task_context(
    *,
    task_id: str,
    skills: list[Skill],
    tool_names: list[str],
    expected_output_schema: str,
    profile_digests: dict[str, str] | None = None,
    specialization: str | None = None,
    policy_ceiling: int | None = None,
    prompt_tool_names: list[str] | None = None,
    effective_policy: dict[str, Any] | None = None,
) -> TaskContextManifest:
    budget = resolve_skill_budget(skills, policy_ceiling=policy_ceiling)
    estimated = skill_bundle_chars(skills)
    if budget is not None and estimated > budget:
        raise BudgetExhaustedError(
            "Skill/profile bundle exceeds max_prompt_chars before model dispatch",
            details={
                "estimated_prompt_chars": estimated,
                "max_prompt_chars": budget,
                "skill_ids": [s.manifest.id for s in skills],
            },
        )
    primary = skills[0] if skills else None
    digests = {s.manifest.id: s.package_digest for s in skills}
    prompt_names = list(prompt_tool_names) if prompt_tool_names is not None else list(tool_names)
    return TaskContextManifest(
        task_id=task_id,
        primary_skill_id=primary.manifest.id if primary else None,
        primary_skill_digest=primary.package_digest if primary else None,
        skill_digests=digests,
        specialization=specialization,
        profile_digests=dict(profile_digests or {}),
        tool_grant_set=sorted(tool_names),
        prompt_tool_names=sorted(prompt_names),
        effective_policy=effective_policy,
        schema_expectations={"expected_output_schema": expected_output_schema},
        estimated_prompt_chars=estimated,
        max_prompt_chars=budget,
    )


def persist_task_context(manifest: TaskContextManifest, prompts_dir: Path) -> Path:
    prompts_dir.mkdir(parents=True, exist_ok=True)
    path = prompts_dir / f"task-context-{manifest.task_id}.json"
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path
