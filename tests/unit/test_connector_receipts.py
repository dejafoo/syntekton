"""Durable connector receipt tests (PM0.B)."""

from __future__ import annotations

from pathlib import Path

from product_factory.connectors.receipts import (
    build_connector_receipt,
    build_source_record,
    persist_connector_receipt,
)
from product_factory.persistence.artifacts import ArtifactStore


def test_receipt_round_trip(tmp_path: Path) -> None:
    receipt = build_connector_receipt(
        connector_id="tavily",
        tool_name="tavily_web_search",
        result_sha256="c" * 64,
        tool_call_id="tc-1",
        task_id="T-001",
        run_id="run-1",
        provenance=[{"source": "https://example.com", "kind": "url", "sha256": "d" * 64}],
    )
    store = ArtifactStore(tmp_path / "artifacts")
    ref = persist_connector_receipt(store, receipt, created_by_task_id="T-001")
    assert ref.schema_id == "connector_receipt.v1"
    assert store.exists(ref.sha256)
    record = build_source_record(
        source="https://example.com",
        source_type="url",
        sha256="d" * 64,
        connector_id="tavily",
        tool_call_id="tc-1",
    )
    assert record["schema_id"] == "source_record.v1"
