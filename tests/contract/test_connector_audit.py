"""Connector audit events must survive to durable storage in a readable shape.

An external call that leaves no trace is indistinguishable from one that never
happened, so these assert the whole path: broker decision → recorder → SQLite
`events` and the per-run `events.jsonl` mirror.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.connectors.broker import (
    EVENT_DENIED,
    EVENT_FAILED,
    EVENT_INVOKED,
    ConnectorBroker,
)
from product_factory.connectors.errors import ConnectorPolicyDenied
from product_factory.connectors.manifest import (
    ConnectorManifest,
    ConnectorToolSpec,
    EgressPolicy,
)
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorInvocation
from product_factory.connectors.result import ConnectorResult, Provenance
from product_factory.observability.contracts import EVENT_TYPES, EventSeverity
from product_factory.observability.events import EventLog
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.persistence.database import Database

CONNECTOR_ID = "audit_fixture"
TOOL_NAME = "audit_search"


def _manifest(**overrides: object) -> ConnectorManifest:
    defaults: dict[str, object] = {
        "connector_id": CONNECTOR_ID,
        "version": "2.1.0",
        "provider": "fixture",
        "tool_class": "web_read",
        "tools": (ConnectorToolSpec(name=TOOL_NAME, description="Search a fixture index"),),
        "egress": EgressPolicy(mode="domains", allowed_domains=("docs.example.com",)),
        "auth_env_var": "AUDIT_FIXTURE_KEY",
        "result_retention": "excerpt",
    }
    defaults.update(overrides)
    return ConnectorManifest(**defaults)  # type: ignore[arg-type]


def _handler(invocation: ConnectorInvocation) -> ConnectorResult:
    url = "https://docs.example.com/guide"
    invocation.assert_egress_allowed(url)
    return ConnectorResult(
        payload={"title": "Guide", "excerpt": "Read-only fixture content"},
        provenance=(Provenance(source=url, kind="url", sha256="abc123"),),
    )


@pytest.fixture
def recorder_env(tmp_path: Path) -> tuple[TelemetryRecorder, Database, Path]:
    db = Database(tmp_path / "db.sqlite")
    db.upsert_run(
        run_id="run-connector",
        workflow_type="repository_investigation",
        status="running",
        request={},
    )
    jsonl_path = tmp_path / "events.jsonl"
    recorder = TelemetryRecorder(db, jsonl=EventLog(jsonl_path), content_dir=tmp_path / "content")
    return recorder, db, jsonl_path


def _broker_with_recorder(
    recorder: TelemetryRecorder,
    *,
    manifest: ConnectorManifest,
    handler: object,
    config: ConnectorsConfig,
    environ: dict[str, str] | None = None,
) -> ConnectorBroker:
    """Wire a dedicated broker audit sink for direct contract tests.

    Production task execution supplies the same callback per invocation through
    ToolBroker so a shared ConnectorBroker never carries run/task attribution.
    """
    from product_factory.connectors.registry import ConnectorRegistry

    registry = ConnectorRegistry()
    registry.register(manifest, handler)  # type: ignore[arg-type]

    def audit(event_type: str, payload: dict) -> None:
        recorder.emit(
            run_id="run-connector",
            event_type=event_type,
            task_id="t-analysis",
            tool_call_id=payload.get("tool_call_id"),
            summary=f"{payload.get('connector_id')}:{payload.get('tool_name')}",
            payload=payload,
            severity=(EventSeverity.INFO if event_type == EVENT_INVOKED else EventSeverity.ERROR),
        )

    return ConnectorBroker(
        registry,
        config=config,
        audit=audit,
        environ=environ if environ is not None else {"AUDIT_FIXTURE_KEY": "k"},
    )


def _enabled() -> ConnectorsConfig:
    return ConnectorsConfig(connectors={CONNECTOR_ID: ConnectorSettings(enabled=True)})


def test_connector_event_types_are_part_of_the_taxonomy() -> None:
    assert {EVENT_INVOKED, EVENT_DENIED, EVENT_FAILED} <= EVENT_TYPES


def test_successful_invocation_persists_provenance_and_hashes(
    recorder_env: tuple[TelemetryRecorder, Database, Path],
) -> None:
    recorder, db, jsonl_path = recorder_env
    broker = _broker_with_recorder(
        recorder, manifest=_manifest(), handler=_handler, config=_enabled()
    )

    result = broker.invoke(
        tool_name=TOOL_NAME,
        arguments={"query": "how to configure"},
        task_id="t-analysis",
        tool_call_id="tc-audit-1",
        run_id="run-connector",
    )

    rows = db.list_events(run_id="run-connector", after_seq=0, limit=10)
    assert [row["event_type"] for row in rows] == [EVENT_INVOKED]
    payload = json.loads(rows[0]["payload_json"])
    assert payload["connector_id"] == CONNECTOR_ID
    assert payload["connector_version"] == "2.1.0"
    assert payload["tool_name"] == TOOL_NAME
    assert payload["policy_decision_id"].startswith("cd-")
    assert payload["arguments_hash"]
    assert payload["result_sha256"] == result["result_sha256"]
    assert payload["provenance"][0]["source"] == "https://docs.example.com/guide"
    assert payload["duration_ms"] >= 0
    assert rows[0]["task_id"] == "t-analysis"
    assert rows[0]["tool_call_id"] == "tc-audit-1"

    mirrored = [
        json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line
    ]
    assert [entry["type"] for entry in mirrored] == [EVENT_INVOKED]
    assert mirrored[0]["payload"]["connector_id"] == CONNECTOR_ID


def test_denied_invocation_persists_the_denial_reason(
    recorder_env: tuple[TelemetryRecorder, Database, Path],
) -> None:
    recorder, db, _ = recorder_env
    broker = _broker_with_recorder(
        recorder,
        manifest=_manifest(),
        handler=_handler,
        config=ConnectorsConfig(),
    )

    with pytest.raises(ConnectorPolicyDenied):
        broker.invoke(
            tool_name=TOOL_NAME,
            arguments={"query": "x"},
            task_id="t-analysis",
            tool_call_id="tc-audit-2",
            run_id="run-connector",
        )

    rows = db.list_events(run_id="run-connector", after_seq=0, limit=10)
    assert [row["event_type"] for row in rows] == [EVENT_DENIED]
    assert rows[0]["severity"] == EventSeverity.ERROR.value
    payload = json.loads(rows[0]["payload_json"])
    assert payload["denial_code"] == "policy_denied"
    assert "not enabled" in payload["error"]


def test_outage_is_recorded_as_failed_not_denied(
    recorder_env: tuple[TelemetryRecorder, Database, Path],
) -> None:
    recorder, db, _ = recorder_env

    def broken(invocation: ConnectorInvocation) -> ConnectorResult:
        raise ConnectionError("upstream down")

    broker = _broker_with_recorder(
        recorder, manifest=_manifest(), handler=broken, config=_enabled()
    )

    with pytest.raises(Exception, match="failed"):
        broker.invoke(
            tool_name=TOOL_NAME,
            arguments={"query": "x"},
            task_id="t-analysis",
            tool_call_id="tc-audit-3",
            run_id="run-connector",
        )

    rows = db.list_events(run_id="run-connector", after_seq=0, limit=10)
    assert [row["event_type"] for row in rows] == [EVENT_FAILED]
    payload = json.loads(rows[0]["payload_json"])
    assert payload["denial_code"] == "unavailable"


def test_a_secret_in_a_connector_result_is_redacted_before_storage(
    recorder_env: tuple[TelemetryRecorder, Database, Path],
) -> None:
    """Retained excerpts go through the same redaction as any other payload."""
    recorder, db, _ = recorder_env
    leaked = "sk-live-abcdef0123456789"

    def leaking(invocation: ConnectorInvocation) -> ConnectorResult:
        return ConnectorResult(payload={"api_key": leaked, "note": "found in a public gist"})

    broker = _broker_with_recorder(
        recorder,
        manifest=_manifest(egress=EgressPolicy(mode="none"), auth_env_var=None),
        handler=leaking,
        config=_enabled(),
    )
    broker.invoke(
        tool_name=TOOL_NAME,
        arguments={"query": "x"},
        task_id="t-analysis",
        tool_call_id="tc-audit-4",
        run_id="run-connector",
    )

    rows = db.list_events(run_id="run-connector", after_seq=0, limit=10)
    stored = rows[0]["payload_json"]
    assert leaked not in stored
