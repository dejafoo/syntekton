"""Durable validation evidence and baseline comparison."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from product_factory.domain.artifacts import ArtifactRef
from product_factory.domain.errors import ToolAuthorizationError
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.schemas import validate_write_payload
from product_factory.validation.parsers import parse_validation_output

VALIDATION_EVIDENCE_SCHEMA_ID = "validation_evidence.v1"
VALIDATION_PROFILE_VERSION = "registered-commands.v1"


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
) -> ValidationEvidence:
    """Persist raw output and a schema-validated normalized evidence artifact."""

    if command_id not in registered_command_ids:
        raise ToolAuthorizationError(
            f"Validation evidence cannot authorize unregistered command {command_id!r}"
        )

    raw_payload = {
        "command_id": command_id,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "truncated": truncated,
    }
    raw_ref = artifact_store.put_json(
        raw_payload,
        logical_name=f"validation-raw-{command_id}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        trust_level="generated",
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
    validate_write_payload(VALIDATION_EVIDENCE_SCHEMA_ID, payload)
    evidence_ref = artifact_store.put_json(
        payload,
        logical_name=f"validation-evidence-{command_id}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        schema_id=VALIDATION_EVIDENCE_SCHEMA_ID,
        schema_version="1",
        trust_level="generated",
        handoff_state="evidence_complete",
    )
    return ValidationEvidence(payload=payload, artifact_ref=evidence_ref, raw_ref=raw_ref)
