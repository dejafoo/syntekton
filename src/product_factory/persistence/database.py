"""SQLite persistence facade and baseline schema (SD3.A).

``Database`` remains the compatibility surface used by orchestration and host
code. Aggregate ownership lives in ``product_factory.persistence.repositories``;
all connections are owned by ``SqliteActor`` with foreign keys enforced.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from product_factory.persistence.connection import SqliteActor, connect
from product_factory.persistence.repositories import (
    ApprovalRepository,
    ArtifactRepository,
    EvaluationRepository,
    EventRepository,
    HandoffRepository,
    RunRepository,
    TaskRepository,
    WorkerRepository,
)

# Re-export for migration baseline and tests.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    base_commit TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    manifest_json TEXT,
    last_progress_at TEXT,
    active_operation TEXT,
    budget_json TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tasks (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    status TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    result_json TEXT,
    started_at TEXT,
    ended_at TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT,
    active_operation TEXT,
    effective_policy_json TEXT,
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS worker_leases (
    run_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    worktree_key TEXT NOT NULL,
    recovery_outcome TEXT,
    released_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS worker_leases_active_worktree
ON worker_leases(worktree_key) WHERE released_at IS NULL;
CREATE INDEX IF NOT EXISTS worker_leases_expiry
ON worker_leases(expires_at) WHERE released_at IS NULL;

CREATE TABLE IF NOT EXISTS task_dependencies (
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    PRIMARY KEY (run_id, task_id, depends_on)
);

CREATE TABLE IF NOT EXISTS model_invocations (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    model_profile TEXT NOT NULL,
    status TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    response_hash TEXT,
    provider TEXT,
    resolved_model_id TEXT,
    prompt_package_hash TEXT,
    started_at TEXT,
    ended_at TEXT,
    latency_ms INTEGER,
    content_refs_json TEXT NOT NULL DEFAULT '[]',
    routing_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    record_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    started_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    sha256 TEXT PRIMARY KEY,
    media_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    logical_name TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    created_by_task_id TEXT,
    trust_level TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS artifact_instances (
    instance_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT '',
    content_class TEXT,
    producer_task_id TEXT,
    producer_tool TEXT,
    producer_validator TEXT,
    event_seq INTEGER,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    schema_id TEXT,
    schema_version TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    display_name TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'mixed',
    capture_level TEXT NOT NULL DEFAULT 'full',
    visibility TEXT NOT NULL DEFAULT 'available',
    retention TEXT NOT NULL DEFAULT 'run',
    truncated INTEGER NOT NULL DEFAULT 0,
    parent_instance_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

CREATE INDEX IF NOT EXISTS artifact_instances_run_sha
ON artifact_instances(run_id, sha256);

CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    task_id TEXT,
    finding_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validator_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    task_id TEXT,
    result_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS model_catalog_cache (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    payload_json TEXT NOT NULL,
    refreshed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    eval_run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    config_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    occurred_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
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

CREATE INDEX IF NOT EXISTS events_run_seq ON events(run_id, seq);
CREATE INDEX IF NOT EXISTS events_type_seq ON events(event_type, seq);
CREATE INDEX IF NOT EXISTS events_task_seq ON events(run_id, task_id, seq);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# Back-compat aliases used by migrations/tests.
def migrate(conn: sqlite3.Connection) -> None:
    """Apply versioned migrations (SD0.A / SD3.A)."""
    from product_factory.persistence.migrations import apply_migrations

    apply_migrations(conn)


class Database:
    """Compatibility facade over aggregate repositories + SqliteActor."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._actor = SqliteActor(self.db_path, migrate=True)
        self.runs = RunRepository(self._actor)
        self.tasks = TaskRepository(self._actor)
        self.workers = WorkerRepository(self._actor)
        self.artifact_meta = ArtifactRepository(self._actor)
        self.handoffs = HandoffRepository(self._actor)
        self.approvals = ApprovalRepository(self._actor)
        self.events = EventRepository(self._actor)
        self.evaluations = EvaluationRepository(self._actor)

    @property
    def conn(self) -> sqlite3.Connection:
        """Persistence-internal connection (tests/migrations only).

        Application and evaluation code must use aggregate repositories.
        """
        return self._actor.connection

    def close(self) -> None:
        self._actor.close()

    def wal_enabled(self) -> bool:
        return self._actor.wal_enabled()

    def upsert_run(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.upsert_run(*args, **kwargs)
    def get_run(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.get_run(*args, **kwargs)
    def set_cancel_requested(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.set_cancel_requested(*args, **kwargs)
    def is_cancel_requested(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.is_cancel_requested(*args, **kwargs)
    def list_runs(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.list_runs(*args, **kwargs)
    def record_invocation(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.record_invocation(*args, **kwargs)
    def list_invocations(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.list_invocations(*args, **kwargs)
    def get_invocation(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.get_invocation(*args, **kwargs)
    def record_tool_call(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.record_tool_call(*args, **kwargs)
    def list_tool_calls(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.list_tool_calls(*args, **kwargs)
    def record_validator_results(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.record_validator_results(*args, **kwargs)
    def list_validator_results(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.list_validator_results(*args, **kwargs)
    def cache_model_catalog(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.cache_model_catalog(*args, **kwargs)
    def get_model_catalog(self, *args: Any, **kwargs: Any) -> Any:
        return self.runs.get_model_catalog(*args, **kwargs)
    def upsert_task(self, *args: Any, **kwargs: Any) -> Any:
        return self.tasks.upsert_task(*args, **kwargs)
    def list_tasks(self, *args: Any, **kwargs: Any) -> Any:
        return self.tasks.list_tasks(*args, **kwargs)
    def list_tasks_in_creation_order(self, *args: Any, **kwargs: Any) -> Any:
        return self.tasks.list_tasks_in_creation_order(*args, **kwargs)
    def get_task(self, *args: Any, **kwargs: Any) -> Any:
        return self.tasks.get_task(*args, **kwargs)
    def list_task_dependencies(self, *args: Any, **kwargs: Any) -> Any:
        return self.tasks.list_task_dependencies(*args, **kwargs)
    def acquire_worker_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.acquire_worker_lease(*args, **kwargs)
    def heartbeat_worker_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.heartbeat_worker_lease(*args, **kwargs)
    def release_worker_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.release_worker_lease(*args, **kwargs)
    def get_worker_lease(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.get_worker_lease(*args, **kwargs)
    def list_expired_worker_leases(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.list_expired_worker_leases(*args, **kwargs)
    def list_unleased_worker_runs(self, *args: Any, **kwargs: Any) -> Any:
        return self.workers.list_unleased_worker_runs(*args, **kwargs)
    def record_artifact(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.record_artifact(*args, **kwargs)
    def list_artifacts(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.list_artifacts(*args, **kwargs)
    def get_artifact(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.get_artifact(*args, **kwargs)
    def record_artifact_instance(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.record_artifact_instance(*args, **kwargs)
    def get_artifact_instance(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.get_artifact_instance(*args, **kwargs)
    def list_artifact_instances(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.list_artifact_instances(*args, **kwargs)
    def get_artifact_instance_by_id(self, *args: Any, **kwargs: Any) -> Any:
        return self.artifact_meta.get_artifact_instance_by_id(*args, **kwargs)
    def insert_handoff_record(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.insert_handoff_record(*args, **kwargs)
    def get_handoff_record(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.get_handoff_record(*args, **kwargs)
    def find_handoff_record(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.find_handoff_record(*args, **kwargs)
    def list_handoff_records_by_run(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.list_handoff_records_by_run(*args, **kwargs)
    def update_handoff_state(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.update_handoff_state(*args, **kwargs)
    def insert_handoff_consumption(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.insert_handoff_consumption(*args, **kwargs)
    def list_handoff_consumptions(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.list_handoff_consumptions(*args, **kwargs)
    def get_handoff_consumption(self, *args: Any, **kwargs: Any) -> Any:
        return self.handoffs.get_handoff_consumption(*args, **kwargs)
    def insert_action_approval(self, *args: Any, **kwargs: Any) -> Any:
        return self.approvals.insert_action_approval(*args, **kwargs)
    def get_action_approval(self, *args: Any, **kwargs: Any) -> Any:
        return self.approvals.get_action_approval(*args, **kwargs)
    def update_action_approval(self, *args: Any, **kwargs: Any) -> Any:
        return self.approvals.update_action_approval(*args, **kwargs)
    def append_event(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.append_event(*args, **kwargs)
    def latest_seq(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.latest_seq(*args, **kwargs)
    def latest_seq_for_run(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.latest_seq_for_run(*args, **kwargs)
    def count_error_events(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.count_error_events(*args, **kwargs)
    def list_events(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.list_events(*args, **kwargs)
    def last_event_at(self, *args: Any, **kwargs: Any) -> Any:
        return self.events.last_event_at(*args, **kwargs)


__all__ = ["SCHEMA_SQL", "Database", "connect", "migrate", "_ensure_column"]
