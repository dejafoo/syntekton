"""Task specification and result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.artifacts import ArtifactRef, ResourceRef
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.capabilities import Capability
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.usage import UsageMetrics


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    source: Literal["baseline_policy", "user_request", "planner"] = "planner"
    severity: Literal["blocking", "major", "minor"] = "blocking"
    verification: Literal[
        "json_schema",
        "static_rule",
        "command",
        "test_suite",
        "artifact_check",
        "evidence_check",
        "llm_review",
        "human_review",
    ]
    responsible_task_ids: list[str] = Field(default_factory=list)
    validator_config: dict[str, Any] = Field(default_factory=dict)


class TaskSpec(BaseModel):
    id: str
    title: str
    capability: Capability
    objective: str
    rationale: str = ""
    dependencies: list[str] = Field(default_factory=list)
    input_refs: list[ResourceRef] = Field(default_factory=list)
    expected_output_schema: str
    required_skills: list[str] = Field(default_factory=list)
    required_tool_classes: set[str] = Field(default_factory=set)
    prohibited_actions: set[str] = Field(default_factory=set)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    preferred_model_profile: str | None = None
    requires_model_independence_from: list[str] = Field(default_factory=list)
    allowed_path_patterns: list[str] = Field(default_factory=lambda: ["**/*"])
    readable_path_patterns: list[str] = Field(default_factory=list)
    writable_path_patterns: list[str] = Field(default_factory=list)
    risk: Literal["low", "medium", "high"] = "low"

    def effective_read_patterns(self) -> list[str]:
        return self.readable_path_patterns or self.allowed_path_patterns

    def effective_write_patterns(self) -> list[str]:
        return self.writable_path_patterns or self.allowed_path_patterns

    budget: TaskBudget = Field(
        default_factory=lambda: TaskBudget(
            max_input_tokens=32_000,
            max_output_tokens=8_000,
            max_tool_calls=30,
            max_repair_attempts=2,
            max_wall_clock_seconds=600,
        )
    )


class TaskResult(BaseModel):
    task_id: str
    status: Literal[
        "success",
        "partial",
        "blocked",
        "failed",
        "budget_exhausted",
    ]
    summary: str
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    evidence_refs: list[ResourceRef] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    validator_results: list[ValidatorResult] = Field(default_factory=list)
    model_profile: str = ""
    resolved_model_id: str = ""
    provider: str = ""
    prompt_package_hash: str = ""
    tool_call_ids: list[str] = Field(default_factory=list)
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
