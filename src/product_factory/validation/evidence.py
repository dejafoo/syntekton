"""Durable validation evidence and baseline comparison."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from product_factory.domain.artifacts import ArtifactRef
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.observability.contracts import CaptureLevel
from product_factory.persistence.artifact_policy import (
    ArtifactInstance,
    resolve_visibility,
    retain_body,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.schemas import validate_write_payload
from product_factory.validation.parsers import parse_validation_output

VALIDATION_EVIDENCE_SCHEMA_ID = "validation_evidence.v1"
VALIDATION_PROFILE_VERSION = "registered-commands.v1"

InstanceRecorder = Callable[[ArtifactInstance], None]


def normalized_outcomes_digest(outcomes: list[dict[str, Any]]) -> str:
    body = json.dumps(outcomes, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def compare_validation_baseline(
    outcomes: list[dict[str, Any]],
    *,
    artifact_store: ArtifactStore | None = None,
    previous_evidence_ref: str | None = None,
    golden_digest: str | None = None,
) -> dict[str, Any]:
    """Compare normalized outcomes with a golden digest or prior evidence artifact."""

    current_digest = normalized_outcomes_digest(outcomes)
    baseline_digest = golden_digest
    source = "golden_digest" if golden_digest else None

    if previous_evidence_ref:
        source = "previous_evidence"
        if artifact_store is None:
            return {
                "status": "unavailable",
                "current_digest": current_digest,
                "baseline_ref": previous_evidence_ref,
                "reason": "artifact_store_not_provided",
            }
        try:
            previous = json.loads(artifact_store.get_text(previous_evidence_ref))
            baseline_digest = normalized_outcomes_digest(
                list(previous.get("normalized_outcomes") or [])
            )
        except (FileNotFoundError, json.JSONDecodeError, AttributeError, TypeError):
            return {
                "status": "unavailable",
                "current_digest": current_digest,
                "baseline_ref": previous_evidence_ref,
                "reason": "invalid_previous_evidence",
            }

    if not baseline_digest:
        return {"status": "no_baseline", "current_digest": current_digest}
    return {
        "status": "unchanged" if baseline_digest == current_digest else "changed",
        "source": source,
        "baseline_digest": baseline_digest,
        "current_digest": current_digest,
        **({"baseline_ref": previous_evidence_ref} if previous_evidence_ref else {}),
    }


@dataclass(frozen=True)
class ValidationEvidence:
    payload: dict[str, Any]
    artifact_ref: ArtifactRef
    raw_ref: ArtifactRef
    raw_instance: ArtifactInstance | None = None
    evidence_instance: ArtifactInstance | None = None


def _parse_capture_level(value: CaptureLevel | str | None) -> CaptureLevel:
    if isinstance(value, CaptureLevel):
        return value
    try:
        return CaptureLevel(str(value or CaptureLevel.FULL))
    except ValueError:
        return CaptureLevel.FULL


def _put_or_synthesize(
    artifact_store: ArtifactStore,
    payload: dict[str, Any],
    *,
    logical_name: str,
    created_by_task_id: str,
    created_by_tool_call_id: str | None,
    schema_id: str | None = None,
    schema_version: str | None = None,
    handoff_state: str | None = None,
    write_body: bool,
) -> ArtifactRef:
    if write_body:
        return artifact_store.put_json(
            payload,
            logical_name=logical_name,
            created_by_task_id=created_by_task_id,
            created_by_tool_call_id=created_by_tool_call_id,
            schema_id=schema_id,
            schema_version=schema_version,
            trust_level="generated",
            handoff_state=handoff_state,
        )
    body = json.dumps(payload, indent=2, default=str, sort_keys=True) + "\n"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return ArtifactRef(
        sha256=digest,
        media_type="application/json",
        size_bytes=0,
        logical_name=logical_name,
        relative_path=f"blobs/{digest}",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        trust_level="generated",
        schema_id=schema_id,
        schema_version=schema_version,
        handoff_state=handoff_state,  # type: ignore[arg-type]
    )


def write_validation_evidence(
    *,
    artifact_store: ArtifactStore,
    command_id: str,
    registered_command_ids: set[str],
    stdout: str,
    stderr: str,
    exit_code: int,
    input_revision: str,
    created_by_task_id: str,
    created_by_tool_call_id: str | None = None,
    sandbox: str | None = None,
    duration_seconds: float | None = None,
    truncated: bool = False,
    profile_version: str = VALIDATION_PROFILE_VERSION,
    previous_evidence_ref: str | None = None,
    golden_digest: str | None = None,
    run_id: str = "",
    capture_level: CaptureLevel | str | None = CaptureLevel.FULL,
    on_instance: InstanceRecorder | None = None,
) -> ValidationEvidence:
    """Persist raw output and a schema-validated normalized evidence artifact.

    Capture policy is applied at write time (ADR-007 / RF3). Normalized reports
    may remain available while raw validation capture is metadata-only or off.
    """

    if command_id not in registered_command_ids:
        raise ToolAuthorizationError(
            f"Validation evidence cannot authorize unregistered command {command_id!r}"
        )

    level = _parse_capture_level(capture_level)
    raw_visibility = resolve_visibility("raw_validation_capture", level)
    # Reports stay readable under metadata; only OFF withholds the report body.
    report_level = CaptureLevel.OFF if level == CaptureLevel.OFF else CaptureLevel.FULL
    if level == CaptureLevel.REDACTED:
        report_level = CaptureLevel.REDACTED
    report_visibility = resolve_visibility("normalized_evidence", report_level)

    raw_full = {
        "command_id": command_id,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "truncated": truncated,
    }
    if raw_visibility == "redacted":
        raw_payload: dict[str, Any] = {
            "command_id": command_id,
            "stdout": "[redacted]",
            "stderr": "[redacted]",
            "exit_code": exit_code,
            "truncated": truncated,
            "redacted": True,
        }
    elif retain_body(raw_visibility):
        raw_payload = raw_full
    else:
        raw_payload = {
            "command_id": command_id,
            "exit_code": exit_code,
            "truncated": truncated,
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
            "body_retained": False,
        }

    raw_ref = _put_or_synthesize(
        artifact_store,
        raw_payload,
        logical_name=f"validation-raw-{command_id}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        write_body=retain_body(raw_visibility) or raw_visibility == "metadata_only",
    )

    parsed = parse_validation_output(
        command_id,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        truncated=truncated,
    )
    outcomes = parsed.normalized_outcomes()
    payload: dict[str, Any] = {
        "profile_version": profile_version,
        "command_id": command_id,
        "receipt": {
            "exit_code": exit_code,
            "sandbox": sandbox,
            "duration_seconds": duration_seconds,
            "tool_call_id": created_by_tool_call_id,
            "parser_id": parsed.parser_id,
            "parser_version": parsed.parser_version,
            "parse_completeness": parsed.completeness,
            "parse_diagnostics": list(parsed.diagnostics),
            "truncated": truncated,
        },
        "input_revision": input_revision or "unknown",
        "normalized_outcomes": outcomes,
        "raw_ref": raw_ref.sha256,
        "baseline_comparison": compare_validation_baseline(
            outcomes,
            artifact_store=artifact_store,
            previous_evidence_ref=previous_evidence_ref,
            golden_digest=golden_digest,
        ),
    }
    if report_visibility == "redacted":
        payload = {
            **payload,
            "normalized_outcomes": [
                {
                    "kind": item.get("kind", "summary"),
                    "status": item.get("status", "unknown"),
                    "message": "[redacted]",
                    "count": item.get("count"),
                }
                for item in outcomes
                if isinstance(item, dict)
            ],
            "redacted": True,
        }
    validate_write_payload(VALIDATION_EVIDENCE_SCHEMA_ID, payload)
    evidence_ref = _put_or_synthesize(
        artifact_store,
        payload,
        logical_name=f"validation-evidence-{command_id}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        schema_id=VALIDATION_EVIDENCE_SCHEMA_ID,
        schema_version="1",
        handoff_state="evidence_complete",
        write_body=retain_body(report_visibility) or report_visibility == "metadata_only",
    )

    raw_instance = None
    evidence_instance = None
    if run_id:
        raw_instance = ArtifactInstance.create(
            run_id=run_id,
            sha256=raw_ref.sha256,
            content_class="raw_validation_capture",
            capture_level=level,
            role="validation_raw",
            producer_task_id=created_by_task_id,
            producer_tool="run_validation_command",
            producer_validator=command_id,
            media_type=raw_ref.media_type,
            size_bytes=raw_ref.size_bytes,
            display_name=raw_ref.logical_name,
            truncated=truncated,
            metadata={"command_id": command_id},
        )
        evidence_instance = ArtifactInstance.create(
            run_id=run_id,
            sha256=evidence_ref.sha256,
            content_class="normalized_evidence",
            capture_level=report_level,
            role="validation_evidence",
            producer_task_id=created_by_task_id,
            producer_tool="run_validation_command",
            producer_validator=command_id,
            media_type=evidence_ref.media_type,
            schema_id=VALIDATION_EVIDENCE_SCHEMA_ID,
            schema_version="1",
            size_bytes=evidence_ref.size_bytes,
            display_name=evidence_ref.logical_name,
            parent_instance_ids=[raw_instance.instance_id],
            metadata={"command_id": command_id, "raw_ref": raw_ref.sha256},
        )
        if on_instance is not None:
            on_instance(raw_instance)
            on_instance(evidence_instance)

    return ValidationEvidence(
        payload=payload,
        artifact_ref=evidence_ref,
        raw_ref=raw_ref,
        raw_instance=raw_instance,
        evidence_instance=evidence_instance,
    )
