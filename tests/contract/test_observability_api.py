"""Contract tests for observability REST, SSE, and WebSocket."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from product_factory.observability.contracts import CaptureLevel
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.persistence.database import Database

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402


@pytest.fixture
def api_env(tmp_path: Path):
    data_dir = tmp_path / ".product-factory"
    (data_dir / "data").mkdir(parents=True)
    (data_dir / "runs").mkdir(parents=True)
    db = Database(data_dir / "data" / "product_factory.sqlite")
    db.upsert_run(
        run_id="run-api",
        workflow_type="code_change",
        status="running",
        request={"budget": {"max_cost_usd": "2"}},
    )
    recorder = TelemetryRecorder(
        db,
        capture_level=CaptureLevel.REDACTED,
        content_dir=data_dir / "runs" / "run-api" / "content",
    )
    for i in range(3):
        recorder.emit(
            run_id="run-api",
            event_type="task.started" if i < 2 else "heartbeat",
            task_id=f"t{i}",
            summary=f"e{i}",
            payload={"i": i, "api_key": "sk-should-redact"},
            content={"message": "hello", "api_key": "sk-secret"} if i == 0 else None,
        )
    metadata = TelemetryRecorder(db, capture_level=CaptureLevel.METADATA)
    metadata.emit(
        run_id="run-api",
        event_type="prompt.package_created",
        summary="metadata-only capture",
        content={"message": "not persisted"},
    )
    db.upsert_task(
        run_id="run-api",
        task_id="t0",
        capability="implementation",
        status="running",
        spec={"title": "impl", "dependencies": []},
    )
    (data_dir / "runs" / "run-api" / "output").mkdir(parents=True)
    (data_dir / "runs" / "run-api" / "output" / "plan.json").write_text(
        json.dumps({"tasks": {"t0": {"id": "t0"}}}), encoding="utf-8"
    )
    (data_dir / "runs" / "run-api" / "output" / "compiler-report.json").write_text(
        json.dumps({"ok": True}), encoding="utf-8"
    )
    body = b"artifact for run api"
    sha = hashlib.sha256(body).hexdigest()
    artifact_path = data_dir / "runs" / "run-api" / "artifacts" / "blobs"
    artifact_path.mkdir(parents=True)
    (artifact_path / sha).write_bytes(body)
    db.record_artifact(
        {
            "sha256": sha,
            "media_type": "text/plain",
            "size_bytes": len(body),
            "logical_name": "report.txt",
            "relative_path": f"blobs/{sha}",
            "created_by_task_id": "t0",
        }
    )
    app = create_app(data_dir)
    with TestClient(app) as client:
        yield client, db, data_dir
    db.close()


def test_health_and_meta(api_env) -> None:
    client, _, _ = api_env
    h = client.get("/api/v1/health")
    assert h.status_code == 200
    body = h.json()
    assert body["wal_mode"] is True
    assert body["latest_seq"] >= 3
    meta = client.get("/api/v1/meta")
    assert meta.json()["api_version"] == "v1"


def test_runs_and_tasks(api_env) -> None:
    client, _, _ = api_env
    runs = client.get("/api/v1/runs").json()
    assert any(r["run_id"] == "run-api" for r in runs)
    detail = client.get("/api/v1/runs/run-api").json()
    assert detail["latest_seq"] >= 3
    assert "api_key" not in json.dumps(detail)
    tasks = client.get("/api/v1/runs/run-api/tasks").json()
    assert tasks[0]["task_id"] == "t0"


def test_events_cursor_and_filter(api_env) -> None:
    client, _, _ = api_env
    page1 = client.get("/api/v1/runs/run-api/events", params={"after_seq": 0, "limit": 2}).json()
    assert len(page1["items"]) == 2
    assert page1["items"][0]["payload"].get("api_key") == "***"
    cursor = page1["next_cursor"]
    page2 = client.get(
        "/api/v1/runs/run-api/events", params={"after_seq": cursor, "limit": 10}
    ).json()
    assert len(page2["items"]) == 2
    filtered = client.get(
        "/api/v1/runs/run-api/events", params={"types": "task.started"}
    ).json()
    assert all(i["type"] == "task.started" for i in filtered["items"])


def test_openapi_contains_v1(api_env) -> None:
    client, _, _ = api_env
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/runs/{run_id}/events" in paths
    assert "/api/v1/runs/{run_id}/costs" in paths
    assert "/api/v1/runs/{run_id}/content/{sha256}" in paths


def test_dashboard_projections_and_scoped_content(api_env) -> None:
    client, db, data_dir = api_env
    plan = client.get("/api/v1/runs/run-api/plan")
    assert plan.status_code == 200
    assert plan.json()["compiler"] == {"ok": True}
    assert client.get("/api/v1/runs/run-api/lineage").json()["dependencies"]["t0"] == []
    costs = client.get("/api/v1/runs/run-api/costs").json()
    assert costs["basis"] == "estimated"

    artifact = client.get("/api/v1/runs/run-api/artifacts").json()[0]
    content = client.get(f"/api/v1/runs/run-api/artifacts/{artifact['sha256']}/content")
    assert content.status_code == 200
    assert content.json()["payload"] == "artifact for run api"

    event = client.get("/api/v1/runs/run-api/events").json()["items"][0]
    content_sha = event["content_refs"][0]["sha256"]
    capture = client.get(f"/api/v1/runs/run-api/content/{content_sha}")
    assert capture.status_code == 200
    assert capture.json()["available"] is True
    assert capture.json()["payload"]["api_key"] == "***"
    assert client.get("/api/v1/runs/run-api/content/" + "0" * 64).status_code == 404
    metadata_event = next(
        item
        for item in client.get("/api/v1/runs/run-api/events", params={"limit": 20}).json()["items"]
        if item["summary"] == "metadata-only capture"
    )
    metadata_sha = metadata_event["content_refs"][0]["sha256"]
    metadata_body = client.get(f"/api/v1/runs/run-api/content/{metadata_sha}")
    assert metadata_body.status_code == 200
    assert metadata_body.json()["available"] is False
    assert metadata_body.json()["capture_level"] == "metadata"

    db.upsert_run(run_id="run-other", workflow_type="code_change", status="completed", request={})
    assert client.get(f"/api/v1/runs/run-other/artifacts/{artifact['sha256']}/content").status_code == 404


def test_dashboard_shell_is_packaged(api_env) -> None:
    client, _, _ = api_env
    response = client.get("/dashboard/")
    assert response.status_code == 200
    assert "Product Factory" in response.text


def test_sse_stream_replay(api_env) -> None:
    client, _, _ = api_env
    # live=false ends after catch-up so TestClient does not hang on an open stream.
    with client.stream(
        "GET",
        "/api/v1/runs/run-api/events/stream",
        params={"after_seq": 0, "live": "false"},
    ) as resp:
        assert resp.status_code == 200
        buf = "".join(resp.iter_text())
    assert "event: task.started" in buf
    assert "data:" in buf


def test_websocket_subscribe_and_replay(api_env) -> None:
    client, _, _ = api_env
    with client.websocket_connect("/api/v1/events/ws") as ws:
        ws.send_json({"run_ids": ["run-api"], "after_seq": 0})
        first = ws.receive_json()
        assert first["type"] == "subscribed"
        seen = []
        for _ in range(5):
            frame = ws.receive_json()
            if frame.get("type") == "event":
                seen.append(frame["event"]["type"])
            if len(seen) >= 3:
                break
        assert "task.started" in seen
