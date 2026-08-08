"""Task specification and result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.artifacts import ArtifactRef, ResourceRef
from product_factory.domain.budgets import TaskBudget
from product_factory.domain.capabilities import Capability
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.usage import UsageMetrics

# Executor completion status (distinct from validation / domain outcome).
#
# success: executor completed and produced contract-valid required output.
#   Domain failure (e.g. failing tests with complete receipts) may still be
#   success; the outcome lives in receipts / validator results / findings.
# partial: required output is incomplete and cannot satisfy a dependency.
# blocked: mandatory evidence, authority, or capability was unavailable
#   before valid execution.
# unsupported: no registered and permitted execution path exists.
# failed: execution was attempted but failed operationally.
# budget_exhausted: task stopped because its budget was exhausted.
# skipped: trusted pack / lifecycle policy intentionally omitted the task
#   (e.g. origin superseded by a successful repair).
TaskResultStatus = Literal[
    "success",
    "partial",
    "blocked",
    "failed",
    "budget_exhausted",
    "unsupported",
    "skipped",
]

_TERMINAL_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "success",
        "partial",
        "blocked",
        "failed",
        "budget_exhausted",
        "unsupported",
        "skipped",
    }
)
_DEPENDENCY_SATISFYING_STATUSES: frozenset[str] = frozenset({"success", "skipped"})
_REPAIR_OR_RESOLUTION_STATUSES: frozenset[str] = frozenset(
    {
        "partial",
        "blocked",
        "failed",
        "budget_exhausted",
        "unsupported",
    }
)


def is_terminal_task_status(status: str) -> bool:
    """Return True when the task will not make further progress."""
    return status in _TERMINAL_TASK_STATUSES


def satisfies_dependency(status: str) -> bool:
    """Return True when dependents may treat this task as complete."""
    return status in _DEPENDENCY_SATISFYING_STATUSES


def requires_repair_or_terminal_resolution(status: str) -> bool:
    """Return True when unsuccessful work needs repair or diagnosis."""
    return status in _REPAIR_OR_RESOLUTION_STATUSES


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

    budget: TaskBudget = Field(default_factory=TaskBudget)


class TaskResult(BaseModel):
    task_id: str
    status: TaskResultStatus
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
    # SD1.B executor receipts — identify who worked and whether it was live.
    executor_mode: str | None = None
    executor_adapter_id: str | None = None
    agent_profile_id: str | None = None
    parser_id: str | None = None
    execution_mode: Literal["live", "deterministic_mock"] | None = None
    activity_receipt: dict[str, Any] = Field(default_factory=dict)
