"""Artifact store and persistence tests."""

from __future__ import annotations

from pathlib import Path

from product_factory.observability.events import EventLog
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database


def test_artifact_dedupe(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    a1 = store.put_text("hello", media_type="text/plain", logical_name="a", created_by_task_id="t")
    a2 = store.put_text("hello", media_type="text/plain", logical_name="b", created_by_task_id="t")
    assert a1.sha256 == a2.sha256
    assert len(list((tmp_path / "artifacts" / "blobs").iterdir())) == 1


def test_database_run_roundtrip(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.upsert_run(
        run_id="run-1",
        workflow_type="code_change",
        status="completed",
        request={"request_id": "r"},
        usage={"estimated_cost_usd": "0.1"},
    )
    row = db.get_run("run-1")
    assert row is not None
    assert row["status"] == "completed"
    db.close()


def test_event_log_append_only(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl")
    log.emit("run-1", "a", {"x": 1})
    log.emit("run-1", "b", {"y": 2})
    events = log.read_all()
    assert len(events) == 2
    assert events[0]["type"] == "a"
