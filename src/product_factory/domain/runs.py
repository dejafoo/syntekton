"""Run request and status contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from product_factory.domain.artifacts import HandoffRef
from product_factory.domain.budgets import RunBudget
from product_factory.domain.usage import UsageMetrics

# SD2: pack ids are registry-validated strings (not a closed Literal).
# Host/API still accept aliases; durable runs persist canonical pack IDs.
WorkflowType = str


class GitRefWorkspace(BaseModel):
    """A server-resolved repository ref; client paths are never accepted."""

    model_config = {"extra": "forbid"}

    kind: Literal["git_ref"] = "git_ref"
    repository_id: str
    ref: str
    commit: str | None = None


class WorkspaceProvenance(BaseModel):
    """Exact repository revision prepared for a run."""

    model_config = {"extra": "forbid"}

    kind: Literal["git_ref"] = "git_ref"
    repository_id: str
    ref: str
    commit: str


class ArtifactOverride(BaseModel):
    """Host-chosen name and/or destination for one pack deliverable role."""

    model_config = {"extra": "forbid"}

    logical_name: str | None = None
    dest_path: str | None = None


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
    "cancelled",
]


class RunRequest(BaseModel):
    request_id: str
    workflow_type: WorkflowType
    request_text: str

    @field_validator("workflow_type")
    @classmethod
    def _validate_workflow_type(cls, value: str) -> str:
        from product_factory.workflows.registry import is_registered_workflow

        if not is_registered_workflow(value):
            raise ValueError(f"Unknown workflow pack: {value!r}")
        return value

    repository_path: Path | None = None
    # Server-registered repository id (remote mode). Resolved to repository_path
    # on the host before execution; clients never send laptop paths remotely.
    repository_id: str | None = None
    workspace: GitRefWorkspace | None = None
    workspace_provenance: WorkspaceProvenance | None = None
    # Deprecated (SD7): ignored; capability routing owns profiles. Retained on
    # RunRequest for host/v1 compatibility until the v1 removal window.
    model_profile_set: str = "local-target"
    validation_commands: list[str] = Field(default_factory=list)
    # Deprecated (SD7): prefer `artifact_overrides`. Still accepted for one
    # compatibility window; host/v2 rejects this field.
    requested_artifacts: list[str] = Field(default_factory=list)
    artifact_overrides: dict[str, ArtifactOverride] = Field(default_factory=dict)
    handoff_refs: list[HandoffRef] = Field(default_factory=list)
    # Typed pack-specific payload, validated at submit against the resolved
    # pack's `input_schema`. Packs without a typed contract leave it empty.
    pack_input: dict[str, Any] = Field(default_factory=dict)
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
    workspace_provenance: WorkspaceProvenance | None = None
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    findings_count: int = 0
    task_count: int = 0
    repair_count: int = 0
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
