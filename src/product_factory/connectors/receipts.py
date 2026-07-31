"""Durable connector / source receipts (PM0.B / S0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from product_factory.domain.artifacts import ArtifactRef
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.schemas import validate_write_payload


def build_connector_receipt(
    *,
    connector_id: str,
    tool_name: str,
    result_sha256: str,
    tool_call_id: str,
    task_id: str,
    run_id: str = "",
    provenance: list[dict[str, Any]] | None = None,
    trust_label: str = "untrusted",
    truncated: bool = False,
    policy_decision_id: str = "",
) -> dict[str, Any]:
    retrieved_at = datetime.now(UTC).isoformat()
    receipt = {
        "schema_id": "connector_receipt.v1",
        "connector_id": connector_id,
        "tool_name": tool_name,
        "result_sha256": result_sha256,
        "retrieved_at": retrieved_at,
        "tool_call_id": tool_call_id,
        "task_id": task_id,
        "run_id": run_id,
        "trust_label": trust_label,
        "truncated": truncated,
        "policy_decision_id": policy_decision_id,
        "provenance": provenance or [],
    }
    validate_write_payload("connector_receipt.v1", receipt)
    return receipt


def build_source_record(
    *,
    source: str,
    source_type: str,
    sha256: str,
    trust_label: str = "untrusted",
    connector_id: str = "",
    tool_call_id: str = "",
    excerpt_start: int = 0,
    excerpt_end: int = 0,
    freshness: str = "retrieved",
    retrieved_at: str | None = None,
    source_class: str | None = None,
    published_at: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_id": "source_record.v1",
        "source": source,
        "source_type": source_type,
        "retrieved_at": retrieved_at or datetime.now(UTC).isoformat(),
        "sha256": sha256,
        "trust_label": trust_label,
        "connector_id": connector_id,
        "tool_call_id": tool_call_id,
        "excerpt_bounds": {"start": excerpt_start, "end": excerpt_end},
        "freshness": freshness,
    }
    if source_class is not None:
        record["source_class"] = source_class
    if published_at is not None:
        record["published_at"] = published_at
    validate_write_payload("source_record.v1", record)
    return record


def persist_connector_receipt(
    store: ArtifactStore,
    receipt: dict[str, Any],
    *,
    created_by_task_id: str,
    created_by_tool_call_id: str | None = None,
) -> ArtifactRef:
    return store.put_json(
        receipt,
        logical_name=f"connector-receipt-{receipt.get('tool_call_id', 'unknown')}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        schema_id="connector_receipt.v1",
        schema_version="1",
        trust_level="untrusted",
    )


def persist_source_records(
    store: ArtifactStore,
    records: list[dict[str, Any]],
    *,
    created_by_task_id: str,
    created_by_tool_call_id: str | None = None,
) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for idx, record in enumerate(records):
        refs.append(
            store.put_json(
                record,
                logical_name=f"source-record-{idx}.json",
                created_by_task_id=created_by_task_id,
                created_by_tool_call_id=created_by_tool_call_id,
                schema_id="source_record.v1",
                schema_version="1",
                trust_level="untrusted",
            )
        )
    return refs


def persist_source_capture(
    store: ArtifactStore,
    body: bytes,
    *,
    url: str,
    media_type: str,
    redirect_chain: list[dict[str, Any]],
    created_by_task_id: str,
    created_by_tool_call_id: str | None = None,
    retrieved_at: str | None = None,
) -> tuple[ArtifactRef, ArtifactRef, dict[str, Any]]:
    """Persist raw source bytes and their typed, lookup-addressable receipt."""
    captured_at = retrieved_at or datetime.now(UTC).isoformat()
    source_ref = store.put_bytes(
        body,
        media_type=media_type,
        logical_name=f"source-{created_by_tool_call_id or 'capture'}",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        trust_level="untrusted",
    )
    capture = {
        "schema_id": "source_capture.v1",
        "url": url,
        "sha256": source_ref.sha256,
        "media_type": media_type,
        "bytes": len(body),
        "retrieved_at": captured_at,
        "redirect_chain": redirect_chain,
        "tool_call_id": created_by_tool_call_id or "",
    }
    validate_write_payload("source_capture.v1", capture)
    capture_ref = store.put_json(
        capture,
        logical_name=f"source-capture-{source_ref.sha256}.json",
        created_by_task_id=created_by_task_id,
        created_by_tool_call_id=created_by_tool_call_id,
        schema_id="source_capture.v1",
        schema_version="1",
        trust_level="untrusted",
    )
    index_dir = store.root / "source-captures"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / f"{source_ref.sha256}.json").write_text(
        json.dumps(capture, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return source_ref, capture_ref, capture
