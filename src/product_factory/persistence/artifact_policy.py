"""Run-scoped artifact instances and capture-policy matrix (ADR-007 / RF3)."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.observability.contracts import CaptureLevel

ContentClass = Literal[
    "durable_output",
    "normalized_evidence",
    "raw_tool_capture",
    "raw_source_capture",
    "raw_validation_capture",
    "model_capture",
]

Visibility = Literal["available", "redacted", "metadata_only", "unavailable", "legacy_unknown"]

UnavailabilityReason = Literal[
    "capture_off",
    "metadata_only",
    "expired",
    "not_retained",
    "legacy_unknown",
]


class ArtifactContentClass(StrEnum):
    DURABLE_OUTPUT = "durable_output"
    NORMALIZED_EVIDENCE = "normalized_evidence"
    RAW_TOOL_CAPTURE = "raw_tool_capture"
    RAW_SOURCE_CAPTURE = "raw_source_capture"
    RAW_VALIDATION_CAPTURE = "raw_validation_capture"
    MODEL_CAPTURE = "model_capture"


# Canonical stored representation by capture level (ADR-007 §4).
# "none" = do not retain a recoverable body.
_MATRIX: dict[str, dict[CaptureLevel, Visibility]] = {
    "durable_output": {
        CaptureLevel.OFF: "metadata_only",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
    "normalized_evidence": {
        CaptureLevel.OFF: "metadata_only",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
    "raw_tool_capture": {
        CaptureLevel.OFF: "unavailable",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
    "raw_source_capture": {
        CaptureLevel.OFF: "unavailable",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
    "raw_validation_capture": {
        CaptureLevel.OFF: "unavailable",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
    "model_capture": {
        CaptureLevel.OFF: "unavailable",
        CaptureLevel.METADATA: "metadata_only",
        CaptureLevel.REDACTED: "redacted",
        CaptureLevel.FULL: "available",
    },
}


def resolve_visibility(
    content_class: str | None,
    capture_level: CaptureLevel | str | None,
) -> Visibility:
    """Map content class × capture level to visibility."""

    if content_class is None:
        return "legacy_unknown"
    try:
        level = (
            capture_level
            if isinstance(capture_level, CaptureLevel)
            else CaptureLevel(str(capture_level or CaptureLevel.METADATA))
        )
    except ValueError:
        level = CaptureLevel.METADATA
    row = _MATRIX.get(content_class)
    if row is None:
        return "legacy_unknown"
    return row[level]


def retain_body(visibility: Visibility) -> bool:
    return visibility in {"available", "redacted"}


def unavailability_reason(
    visibility: Visibility,
    *,
    capture_level: CaptureLevel | None = None,
) -> UnavailabilityReason | None:
    if visibility in {"available", "redacted"}:
        return None
    if visibility == "metadata_only":
        return "metadata_only"
    if visibility == "legacy_unknown":
        return "legacy_unknown"
    if capture_level == CaptureLevel.OFF:
        return "capture_off"
    return "not_retained"


class ArtifactInstance(BaseModel):
    """Run-scoped ownership record for a content-addressed blob."""

    model_config = {"extra": "forbid"}

    instance_id: str = Field(default_factory=lambda: f"ai-{uuid.uuid4().hex[:12]}")
    run_id: str
    sha256: str
    role: str = ""
    content_class: ContentClass | None = None
    producer_task_id: str | None = None
    producer_tool: str | None = None
    producer_validator: str | None = None
    event_seq: int | None = None
    media_type: str = "application/octet-stream"
    schema_id: str | None = None
    schema_version: str | None = None
    size_bytes: int = 0
    display_name: str = ""
    classification: str = "mixed"
    capture_level: CaptureLevel = CaptureLevel.FULL
    visibility: Visibility = "available"
    retention: str = "run"
    truncated: bool = False
    parent_instance_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sha256: str,
        content_class: ContentClass | None,
        capture_level: CaptureLevel | str | None = CaptureLevel.FULL,
        role: str = "",
        producer_task_id: str | None = None,
        producer_tool: str | None = None,
        producer_validator: str | None = None,
        media_type: str = "application/octet-stream",
        schema_id: str | None = None,
        schema_version: str | None = None,
        size_bytes: int = 0,
        display_name: str = "",
        classification: str = "mixed",
        truncated: bool = False,
        parent_instance_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactInstance:
        level = (
            capture_level
            if isinstance(capture_level, CaptureLevel)
            else CaptureLevel(str(capture_level or CaptureLevel.FULL))
        )
        visibility = resolve_visibility(content_class, level)
        return cls(
            run_id=run_id,
            sha256=sha256,
            role=role,
            content_class=content_class,
            producer_task_id=producer_task_id,
            producer_tool=producer_tool,
            producer_validator=producer_validator,
            media_type=media_type,
            schema_id=schema_id,
            schema_version=schema_version,
            size_bytes=size_bytes,
            display_name=display_name or role or sha256[:12],
            classification=classification,
            capture_level=level,
            visibility=visibility,
            truncated=truncated,
            parent_instance_ids=list(parent_instance_ids or []),
            metadata=dict(metadata or {}),
        )
