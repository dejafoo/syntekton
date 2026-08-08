"""Artifact and resource reference contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

HandoffState = Literal["draft", "evidence_complete", "approved", "superseded"]


class ArtifactRef(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    media_type: str
    size_bytes: int = Field(ge=0)
    logical_name: str
    relative_path: str
    created_by_task_id: str
    created_by_tool_call_id: str | None = None
    trust_level: Literal["trusted", "untrusted", "mixed", "generated"] = "generated"
    schema_id: str | None = None
    schema_version: str | None = None
    handoff_state: HandoffState | None = None


class HandoffRef(BaseModel):
    """Cross-run typed handoff pointer (PM0 shape; chaining lands later)."""

    schema_id: str
    digest: str = Field(min_length=64, max_length=64)
    producer_run_id: str
    producer_task_id: str
    role: str
    state: HandoffState = "draft"
    metadata: dict[str, object] = Field(default_factory=dict)


class ResourceRef(BaseModel):
    id: str
    resource_type: Literal[
        "repository",
        "file",
        "directory",
        "artifact",
        "task_result",
        "test_result",
        "patch",
    ]
    origin: Literal[
        "user",
        "run_coordinator",
        "tool",
        "task",
        "validator",
    ]
    scope: str
    trust_level: Literal["trusted", "untrusted", "mixed"] = "untrusted"
    content_hash: str | None = None
    created_by_tool_call_id: str | None = None
