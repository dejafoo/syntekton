"""Run request and status contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.budgets import RunBudget
from product_factory.domain.usage import UsageMetrics

WorkflowType = Literal["architecture", "code_change", "repository_change"]

FinalStatus = Literal[
    "initializing",
    "planning",
    "plan_rejected",
    "executing",
    "validating",
    "repairing",
    "awaiting_approval",
    "completed",
    "failed",
    "blocked",
    "budget_exhausted",
]


class RunRequest(BaseModel):
    request_id: str
    workflow_type: WorkflowType
    request_text: str
    repository_path: Path | None = None
    project_profile: str = "default"
    model_profile_set: str = "local-target"
    validation_commands: list[str] = Field(default_factory=list)
    requested_artifacts: list[str] = Field(default_factory=list)
    budget: RunBudget = Field(default_factory=RunBudget)
    approval_policy: str = "manual_apply"
    metadata: dict[str, str] = Field(default_factory=dict)


class RunManifest(BaseModel):
    run_id: str
    request: RunRequest
    final_status: FinalStatus
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    ended_at: datetime | None = None
    base_commit: str | None = None
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    findings_count: int = 0
    task_count: int = 0
    repair_count: int = 0
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
