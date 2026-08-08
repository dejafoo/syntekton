"""RF6 additive migration + package/observe smoke against supported schemas.

SD0.A refuses partial/unknown legacy schemas. Supported upgrade path is a full
pre-SD0 (post-RF6 SCHEMA_SQL) database without schema_migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from product_factory.persistence.database import SCHEMA_SQL, Database, migrate
from product_factory.persistence.migrations import MigrationError

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from product_factory.api.app import create_app  # noqa: E402


def _create_partial_legacy_db(path: Path) -> None:
    """Minimal pre-RF schema — intentionally unsupported after SD0.A."""
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


def _create_pre_sd0_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        """
        INSERT INTO runs (run_id, workflow_type, status, request_json, usage_json, created_at, updated_at)
        VALUES ('legacy-run', 'code_change', 'succeeded', '{}', '{}', '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z')
        """
    )
    conn.execute(
        """
        INSERT INTO tasks (run_id, task_id, capability, status, spec_json)
        VALUES ('legacy-run', 't0', 'implementation', 'success', '{"title":"legacy"}')
        """
    )
    conn.commit()
    conn.close()


def test_partial_legacy_schema_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / ".product-factory" / "data" / "product_factory.sqlite"
    _create_partial_legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    with pytest.raises(MigrationError, match="partial legacy schema|Unsupported"):
        migrate(conn)
    conn.close()


def test_migrate_upgrades_pre_sd0_sqlite_in_place(tmp_path: Path) -> None:
    db_path = tmp_path / ".product-factory" / "data" / "product_factory.sqlite"
    _create_pre_sd0_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    migrate(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "schema_migrations" in tables
    assert "legacy_approvals" in tables
    assert "handoff_records" in tables
    assert "action_approvals" in tables
    row = conn.execute("SELECT run_id, status FROM runs WHERE run_id='legacy-run'").fetchone()
    assert row["status"] == "succeeded"
    conn.close()


def test_observe_api_reads_upgraded_pre_sd0_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / ".product-factory"
    db_path = data_dir / "data" / "product_factory.sqlite"
    _create_pre_sd0_db(db_path)
    (data_dir / "runs" / "legacy-run").mkdir(parents=True)
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
