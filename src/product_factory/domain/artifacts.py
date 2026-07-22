"""Artifact and resource reference contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ArtifactRef(BaseModel):
    sha256: str = Field(min_length=64, max_length=64)
    media_type: str
    size_bytes: int = Field(ge=0)
    logical_name: str
    relative_path: str
    created_by_task_id: str
    created_by_tool_call_id: str | None = None
    trust_level: Literal["trusted", "untrusted", "mixed", "generated"] = "generated"


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
