"""RF6 additive migration + package/observe smoke against an old layout."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from product_factory.persistence.database import Database, migrate

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402


def _create_legacy_db(path: Path) -> None:
    """Minimal pre-RF schema: runs/tasks/events without RF2–RF6 columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            workflow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            base_commit TEXT,
            usage_json TEXT NOT NULL DEFAULT '{}',
            manifest_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE tasks (
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            capability TEXT NOT NULL,
            status TEXT NOT NULL,
            spec_json TEXT NOT NULL,
            result_json TEXT,
            PRIMARY KEY (run_id, task_id)
        );
        CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            schema_version INTEGER NOT NULL,
            occurred_at TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            run_id TEXT NOT NULL,
            task_id TEXT,
            request_id TEXT,
            tool_call_id TEXT,
            trace_id TEXT,
            span_id TEXT,
            parent_span_id TEXT,
            severity TEXT NOT NULL,
            summary TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            content_refs_json TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE model_invocations (
            request_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            model_profile TEXT NOT NULL,
            status TEXT NOT NULL,
            usage_json TEXT NOT NULL,
            response_hash TEXT,
            created_at TEXT NOT NULL
        );
        INSERT INTO runs (run_id, workflow_type, status, request_json, usage_json, created_at, updated_at)
        VALUES ('legacy-run', 'code_change', 'succeeded', '{}', '{}', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z');
        INSERT INTO tasks (run_id, task_id, capability, status, spec_json)
        VALUES ('legacy-run', 't0', 'implementation', 'success', '{"title":"legacy"}');
        """
    )
    conn.commit()
    conn.close()


def test_migrate_upgrades_legacy_sqlite_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / ".product-factory" / "data" / "product_factory.sqlite"
    _create_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    task_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    inv_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(model_invocations)").fetchall()
    }
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "effective_policy_json" in task_cols
    assert "routing_json" in inv_cols
    assert "artifact_instances" in tables
    row = conn.execute("SELECT run_id, status FROM runs WHERE run_id='legacy-run'").fetchone()
    assert row["status"] == "succeeded"
    conn.close()


def test_observe_api_reads_upgraded_legacy_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / ".product-factory"
    db_path = data_dir / "data" / "product_factory.sqlite"
    _create_legacy_db(db_path)
    (data_dir / "runs" / "legacy-run").mkdir(parents=True)
    # Opening Database runs migrate(); create_app builds its own Database.
    Database(db_path).close()

    app = create_app(data_dir)
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        runs = client.get("/api/v1/runs").json()
        assert any(run["run_id"] == "legacy-run" for run in runs)
        tasks = client.get("/api/v1/runs/legacy-run/tasks").json()
        assert tasks[0]["task_id"] == "t0"
        assert tasks[0]["legacy_policy"] is True
        costs = client.get("/api/v1/runs/legacy-run/costs").json()
        assert "by_route" in costs
        openapi = client.get("/openapi.json").json()
        task_schema = openapi["components"]["schemas"]["TaskSummary"]
        assert "effective_policy" in task_schema["properties"]
        assert "legacy_policy" in task_schema["properties"]
        inv_schema = openapi["components"]["schemas"]["ModelInvocationView"]
        assert "route" in inv_schema["properties"]
        assert "fallback_reason" in inv_schema["properties"]
