"""Tool registry contracts and capability grants."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskClass = Literal["R0", "R1", "R2", "R3", "R4", "R5", "R6"]


class ToolDefinition(BaseModel):
    name: str
    description: str
    tool_class: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=dict)
    risk_class: RiskClass
    required_capability: str | None = None
    resource_scope: str = "task"
    idempotent: bool = True
    timeout_seconds: int = 60
    requires_human_approval: bool = False
    result_may_be_untrusted: bool = True


class CapabilityGrant(BaseModel):
    grant_id: str
    run_id: str
    task_id: str
    agent_profile: str
    tool_names: set[str]
    resource_scopes: list[str] = Field(default_factory=list)
    allowed_path_patterns: list[str] = Field(default_factory=list)
    readable_path_patterns: list[str] = Field(default_factory=list)
    writable_path_patterns: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    max_calls: int = 100
    approval_id: str | None = None
    calls_made: int = 0

    def read_patterns(self) -> list[str]:
        return self.readable_path_patterns or self.allowed_path_patterns

    def write_patterns(self) -> list[str]:
        return self.writable_path_patterns or self.allowed_path_patterns


class ToolCallRecord(BaseModel):
    tool_call_id: str
    tool_name: str
    task_id: str
    arguments_hash: str
    resource_scope: str
    duration_ms: int
    exit_status: int
    output_artifact_ref: str | None = None
    trust_label: Literal["trusted", "untrusted", "mixed", "generated"] = "untrusted"
    error: str | None = None
