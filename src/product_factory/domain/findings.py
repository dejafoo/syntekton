"""Findings and validator result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.artifacts import ArtifactRef, ResourceRef


class Finding(BaseModel):
    id: str
    criterion_id: str | None = None
    category: Literal[
        "correctness",
        "security",
        "maintainability",
        "test_gap",
        "architecture",
        "requirements",
        "policy",
        "evidence",
        "tool_error",
    ]
    status: Literal["open", "resolved", "accepted_risk"] = "open"
    severity: Literal["blocking", "major", "minor"]
    summary: str
    explanation: str
    evidence_refs: list[ResourceRef] = Field(default_factory=list)
    affected_artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    recommended_action: str | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    produced_by: str


class ValidatorResult(BaseModel):
    validator_id: str
    status: Literal["pass", "fail", "skip", "error"]
    message: str
    evidence_refs: list[ResourceRef] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
