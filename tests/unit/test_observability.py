"""Unit tests for observability contracts, redaction, stuck detection, store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from product_factory.observability.contracts import CaptureLevel, ObservabilityEvent
from product_factory.observability.query import ObservabilityQueryService
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.observability.redaction import capture_content, redact_value
from product_factory.observability.stuck import derive_liveness
from product_factory.persistence.database import Database


def test_redact_sensitive_keys() -> None:
    out = redact_value({"api_key": "sk-secret", "ok": "hello", "nested": {"token": "x"}})
    assert out["api_key"] == "***"
    assert out["nested"]["token"] == "***"
    assert out["ok"] == "hello"


def test_capture_levels(tmp_path: Path) -> None:
    payload = {"messages": [{"role": "user", "content": "hi sk-abcdefghijklmnop"}]}
    ref_meta, body_meta = capture_content(payload, level=CaptureLevel.METADATA)
    assert ref_meta is not None
    assert body_meta is None
    ref_red, body_red = capture_content(payload, level=CaptureLevel.REDACTED)
    assert ref_red is not None
    assert body_red is not None
    assert "sk-" not in json_dumps(body_red)
    ref_full, body_full = capture_content(payload, level=CaptureLevel.FULL)
    assert ref_full is not None
    assert body_full is not None


def json_dumps(value) -> str:
    import json

    return json.dumps(value)


def test_stuck_derivation() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        derive_liveness(status="running", last_progress_at=now.isoformat(), now=now).value
        == "healthy"
    )
    slow = (now - timedelta(seconds=90)).isoformat()
    assert derive_liveness(status="running", last_progress_at=slow, now=now).value == "slow"
    stuck = (now - timedelta(seconds=200)).isoformat()
    assert (
        derive_liveness(status="running", last_progress_at=stuck, now=now).value
        == "suspected_stuck"
    )
    assert derive_liveness(status="completed", last_progress_at=stuck, now=now).value == "healthy"


def test_event_cursor_pagination(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    recorder = TelemetryRecorder(db, capture_level=CaptureLevel.METADATA)
    for i in range(5):
        recorder.emit(run_id="run-1", event_type="heartbeat", summary=str(i), payload={"i": i})
    rows = db.list_events(run_id="run-1", after_seq=0, limit=2)
    assert len(rows) == 2
    assert rows[0]["seq"] == 1
    more = db.list_events(run_id="run-1", after_seq=rows[-1]["seq"], limit=10)
    assert len(more) == 3
    assert more[0]["seq"] == 3
    assert db.latest_seq() == 5


def test_query_run_summary(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.upsert_run(
        run_id="r1",
        workflow_type="code_change",
        status="running",
        request={"budget": {"max_cost_usd": "1.0"}},
    )
    recorder = TelemetryRecorder(db)
    ev = recorder.emit(run_id="r1", event_type="run.started", summary="go")
    assert isinstance(ev, ObservabilityEvent)
    assert ev is not None and ev.seq == 1
    q = ObservabilityQueryService(db, data_dir=tmp_path)
    run = q.get_run("r1")
    assert run is not None
    assert run.latest_seq == 1
    assert run.budget["max_cost_usd"] == "1.0"
