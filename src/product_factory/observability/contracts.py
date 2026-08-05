"""Versioned observability event contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

ENVELOPE_VERSION = 1


class CaptureLevel(StrEnum):
    OFF = "off"
    METADATA = "metadata"
    REDACTED = "redacted"
    FULL = "full"


class EventSeverity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ContentRef(BaseModel):
    """Reference to a content-addressed payload (prompt, response, tool I/O)."""

    sha256: str
    media_type: str = "application/json"
    byte_count: int = 0
    capture_level: CaptureLevel = CaptureLevel.METADATA
    preview: str | None = None
    logical_name: str | None = None


class ObservabilityEvent(BaseModel):
    """Canonical domain event for orchestration observability."""

    event_id: str
    seq: int | None = None
    schema_version: int = ENVELOPE_VERSION
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    type: str
    run_id: str
    task_id: str | None = None
    request_id: str | None = None
    tool_call_id: str | None = None
    trace_id: str | None = None
    span_id: str | None = None
    parent_span_id: str | None = None
    severity: EventSeverity = EventSeverity.INFO
    summary: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    content_refs: list[ContentRef] = Field(default_factory=list)


class Liveness(StrEnum):
    HEALTHY = "healthy"
    SLOW = "slow"
    SUSPECTED_STUCK = "suspected_stuck"
    TIMED_OUT = "timed_out"


class RunSummary(BaseModel):
    run_id: str
    workflow_type: str
    status: str
    created_at: str | None = None
    updated_at: str | None = None
    base_commit: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    task_counts: dict[str, int] = Field(default_factory=dict)
    latest_seq: int = 0
    last_progress_at: str | None = None
    liveness: Liveness = Liveness.HEALTHY
    active_operation: str | None = None
    error_count: int = 0
    next_action: str | None = None


class TaskSummary(BaseModel):
    run_id: str
    task_id: str
    capability: str
    status: str
    title: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    model_profile: str | None = None
    agent_profile: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    attempt: int = 1
    summary: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    liveness: Liveness = Liveness.HEALTHY
    active_operation: str | None = None
    # RF6 additive projections (absent/null on legacy rows).
    effective_policy: dict[str, Any] | None = None
    route_class: str | None = None
    primary_model_profile: str | None = None
    fallback_model_profile: str | None = None
    fallback_eligible: bool | None = None
    stack_profile_digest: str | None = None
    legacy_policy: bool = False
    next_action: str | None = None


class ModelInvocationView(BaseModel):
    request_id: str
    run_id: str
    task_id: str
    model_profile: str
    status: str
    provider: str | None = None
    resolved_model_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    response_hash: str | None = None
    prompt_package_hash: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    latency_ms: int | None = None
    content_refs: list[ContentRef] = Field(default_factory=list)
    route: str | None = None
    primary_profile: str | None = None
    fallback_profile: str | None = None
    fallback_reason: str | None = None
    cost_basis: str | None = None
    cost_usd: str | None = None
    routing: dict[str, Any] = Field(default_factory=dict)


class ToolCallView(BaseModel):
    tool_call_id: str
    run_id: str
    task_id: str
    tool_name: str
    status: str
    arguments_hash: str | None = None
    duration_ms: int | None = None
    exit_status: int | None = None
    output_artifact_ref: str | None = None
    error: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class ArtifactView(BaseModel):
    sha256: str
    media_type: str
    size_bytes: int
    logical_name: str
    relative_path: str | None = None
    created_by_task_id: str | None = None
    trust_level: str = "generated"
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_class: str | None = None
    visibility: str | None = None
    capture_level: str | None = None
    producer_role: str | None = None
    producer_tool: str | None = None
    legacy: bool = False


class PromptPackageView(BaseModel):
    run_id: str
    task_id: str
    package_hash: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    content_refs: list[ContentRef] = Field(default_factory=list)


class ContentView(BaseModel):
    """A run-scoped, capture-policy-aware stored body."""

    sha256: str
    available: bool
    capture_level: CaptureLevel
    media_type: str | None = None
    byte_count: int | None = None
    truncated: bool = False
    payload: Any | None = None
    redacted: bool = False
    reason: str | None = None
    visibility: str | None = None
    content_class: str | None = None
    legacy: bool = False


class PlanView(BaseModel):
    run_id: str
    plan: dict[str, Any] | None = None
    compiler: dict[str, Any] | None = None


class LineageView(BaseModel):
    run_id: str
    dependencies: dict[str, list[str]] = Field(default_factory=dict)
    repairs: list[dict[str, Any]] = Field(default_factory=list)
    files: list[dict[str, Any]] = Field(default_factory=list)


class CostView(BaseModel):
    run_id: str
    basis: Literal["reported", "estimated", "mixed"]
    total: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    ledger: dict[str, Any] = Field(default_factory=dict)
    by_task: list[dict[str, Any]] = Field(default_factory=list)
    by_model: list[dict[str, Any]] = Field(default_factory=list)
    by_route: list[dict[str, Any]] = Field(default_factory=list)


class HealthView(BaseModel):
    status: Literal["ok", "degraded"]
    database_path: str
    wal_mode: bool
    latest_seq: int
    last_event_at: str | None = None
    writer_fresh: bool = True
    capture_level: CaptureLevel = CaptureLevel.REDACTED


# Event type taxonomy (v1)
EVENT_TYPES = frozenset(
    {
        "run.started",
        "run.status_changed",
        "run.finished",
        "run.failed",
        "run.no_progress",
        "repository.snapshot",
        "plan.compiled",
        "plan.rejected",
        "task.started",
        "task.completed",
        "task.failed",
        "prompt.package_created",
        "model.request.started",
        "model.request.completed",
        "model.request.failed",
        "tool.call.started",
        "tool.call.completed",
        "tool.call.failed",
        "connector.invoked",
        "connector.denied",
        "connector.failed",
        "validation.completed",
        "artifact.created",
        "artifact.materialized",
        "approval.required",
        "approval.decided",
        "budget.updated",
        "heartbeat",
        "observability.degraded",
    }
)
