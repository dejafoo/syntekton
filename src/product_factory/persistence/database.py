"""SQLite persistence and schema."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_factory.observability.contracts import ObservabilityEvent

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
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);

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


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: API stream workers call reads via asyncio.to_thread.
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    # Additive migrations for existing DBs created before observability columns.
    _ensure_column(conn, "runs", "last_progress_at", "TEXT")
    _ensure_column(conn, "runs", "active_operation", "TEXT")
    _ensure_column(conn, "runs", "budget_json", "TEXT")
    _ensure_column(conn, "runs", "cancel_requested", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "tasks", "started_at", "TEXT")
    _ensure_column(conn, "tasks", "ended_at", "TEXT")
    _ensure_column(conn, "tasks", "attempt", "INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "tasks", "updated_at", "TEXT")
    _ensure_column(conn, "tasks", "active_operation", "TEXT")
    _ensure_column(conn, "model_invocations", "provider", "TEXT")
    _ensure_column(conn, "model_invocations", "resolved_model_id", "TEXT")
    _ensure_column(conn, "model_invocations", "prompt_package_hash", "TEXT")
    _ensure_column(conn, "model_invocations", "started_at", "TEXT")
    _ensure_column(conn, "model_invocations", "ended_at", "TEXT")
    _ensure_column(conn, "model_invocations", "latency_ms", "INTEGER")
    _ensure_column(conn, "model_invocations", "content_refs_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "tool_calls", "status", "TEXT NOT NULL DEFAULT 'completed'")
    _ensure_column(conn, "tool_calls", "started_at", "TEXT")
    _ensure_column(conn, "tool_calls", "ended_at", "TEXT")
    conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _synchronized(method: Any) -> Any:
    """Serialize writes on the shared connection across concurrent wave threads (P1.F)."""

    def wrapper(self: Database, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    wrapper.__name__ = getattr(method, "__name__", "wrapper")
    wrapper.__doc__ = method.__doc__
    return wrapper


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = connect(db_path)
        migrate(self.conn)
        # Concurrent waves (P1.F) call into this connection from multiple
        # worker threads; serialize writes rather than rely on SQLite's
        # own locking, which can otherwise surface as transient
        # "database is locked" errors even under WAL + busy_timeout.
        self._lock = threading.RLock()

    def close(self) -> None:
        self.conn.close()

    def wal_enabled(self) -> bool:
        row = self.conn.execute("PRAGMA journal_mode").fetchone()
        return bool(row and str(row[0]).lower() == "wal")

    @_synchronized
    def upsert_run(
        self,
        *,
        run_id: str,
        workflow_type: str,
        status: str,
        request: dict[str, Any],
        base_commit: str | None = None,
        usage: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
        active_operation: str | None = None,
        touch_progress: bool = True,
        budget_snapshot: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self.conn.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        progress = now if touch_progress else None
        budget_json = json.dumps(budget_snapshot, default=str) if budget_snapshot else None
        if existing:
            self.conn.execute(
                """
                UPDATE runs SET status=?, updated_at=?, base_commit=?, usage_json=?,
                  manifest_json=?,
                  last_progress_at=COALESCE(?, last_progress_at),
                  active_operation=COALESCE(?, active_operation),
                  budget_json=COALESCE(?, budget_json)
                WHERE run_id=?
                """,
                (
                    status,
                    now,
                    base_commit,
                    json.dumps(usage or {}),
                    json.dumps(manifest) if manifest else None,
                    progress,
                    active_operation,
                    budget_json,
                    run_id,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO runs
                (run_id, workflow_type, status, request_json, created_at, updated_at,
                 base_commit, usage_json, manifest_json, last_progress_at, active_operation,
                 budget_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    workflow_type,
                    status,
                    json.dumps(request, default=str),
                    now,
                    now,
                    base_commit,
                    json.dumps(usage or {}),
                    json.dumps(manifest) if manifest else None,
                    now,
                    active_operation,
                    budget_json,
                ),
            )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    @_synchronized
    def set_cancel_requested(self, run_id: str, *, requested: bool = True) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            UPDATE runs SET cancel_requested=?, updated_at=?
            WHERE run_id=?
            """,
            (1 if requested else 0, now, run_id),
        )
        self.conn.commit()

    def is_cancel_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        if not row:
            return False
        return bool(int(row.get("cancel_requested") or 0))

    def list_runs(self, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def upsert_task(
        self,
        *,
        run_id: str,
        task_id: str,
        capability: str,
        status: str,
        spec: dict[str, Any],
        result: dict[str, Any] | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        attempt: int | None = None,
        active_operation: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self.conn.execute(
            "SELECT attempt, started_at FROM tasks WHERE run_id=? AND task_id=?",
            (run_id, task_id),
        ).fetchone()
        if existing:
            next_attempt = attempt if attempt is not None else int(existing["attempt"] or 1)
            self.conn.execute(
                """
                UPDATE tasks SET capability=?, status=?, spec_json=?, result_json=?,
                  started_at=COALESCE(?, started_at),
                  ended_at=COALESCE(?, ended_at),
                  attempt=?,
                  updated_at=?,
                  active_operation=?
                WHERE run_id=? AND task_id=?
                """,
                (
                    capability,
                    status,
                    json.dumps(spec, default=str),
                    json.dumps(result, default=str) if result else None,
                    started_at,
                    ended_at,
                    next_attempt,
                    now,
                    active_operation,
                    run_id,
                    task_id,
                ),
            )
        else:
            self.conn.execute(
                """
                INSERT INTO tasks
                (run_id, task_id, capability, status, spec_json, result_json,
                 started_at, ended_at, attempt, updated_at, active_operation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task_id,
                    capability,
                    status,
                    json.dumps(spec, default=str),
                    json.dumps(result, default=str) if result else None,
                    started_at or (now if status == "running" else None),
                    ended_at,
                    attempt or 1,
                    now,
                    active_operation,
                ),
            )
        for dep in spec.get("dependencies") or []:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO task_dependencies (run_id, task_id, depends_on)
                VALUES (?, ?, ?)
                """,
                (run_id, task_id, dep),
            )
        self.conn.commit()

    def list_tasks(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY task_id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def list_tasks_in_creation_order(self, run_id: str) -> list[dict[str, Any]]:
        """Tasks ordered by first-insert (rowid), a valid topological order.

        Used by durable resume (P1.B) to rebuild the live plan's task_order:
        a task is only ever inserted once its dependencies have already
        succeeded/skipped, so insertion order is always dependency-consistent.
        """
        rows = self.conn.execute(
            "SELECT rowid AS _rowid, * FROM tasks WHERE run_id = ? ORDER BY _rowid", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, run_id: str, task_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE run_id = ? AND task_id = ?", (run_id, task_id)
        ).fetchone()
        return dict(row) if row else None

    def list_task_dependencies(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM task_dependencies WHERE run_id = ?", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def record_invocation(
        self,
        *,
        request_id: str,
        run_id: str,
        task_id: str,
        model_profile: str,
        status: str,
        usage: dict[str, Any],
        response_hash: str | None,
        provider: str | None = None,
        resolved_model_id: str | None = None,
        prompt_package_hash: str | None = None,
        started_at: str | None = None,
        ended_at: str | None = None,
        latency_ms: int | None = None,
        content_refs: list[dict[str, Any]] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO model_invocations
            (request_id, run_id, task_id, model_profile, status,
             usage_json, response_hash, provider, resolved_model_id,
             prompt_package_hash, started_at, ended_at, latency_ms,
             content_refs_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request_id,
                run_id,
                task_id,
                model_profile,
                status,
                json.dumps(usage, default=str),
                response_hash,
                provider,
                resolved_model_id,
                prompt_package_hash,
                started_at,
                ended_at or now,
                latency_ms,
                json.dumps(content_refs or []),
                now,
            ),
        )
        self.conn.commit()

    def list_invocations(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM model_invocations WHERE run_id = ? ORDER BY created_at",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_invocation(self, request_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM model_invocations WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None

    @_synchronized
    def record_tool_call(
        self,
        *,
        run_id: str,
        record: dict[str, Any],
        status: str = "completed",
        started_at: str | None = None,
        ended_at: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO tool_calls
            (tool_call_id, run_id, task_id, tool_name, record_json, status,
             started_at, ended_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["tool_call_id"],
                run_id,
                record["task_id"],
                record["tool_name"],
                json.dumps(record, default=str),
                status,
                started_at,
                ended_at or now,
                now,
            ),
        )
        self.conn.commit()

    def list_tool_calls(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def record_artifact(self, artifact: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO artifacts
            (sha256, media_type, size_bytes, logical_name, relative_path,
             created_by_task_id, trust_level, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact["sha256"],
                artifact["media_type"],
                artifact["size_bytes"],
                artifact["logical_name"],
                artifact.get("relative_path", ""),
                artifact.get("created_by_task_id"),
                artifact.get("trust_level", "generated"),
                json.dumps(artifact.get("metadata", {})),
            ),
        )
        self.conn.commit()

    def list_artifacts(self, *, created_by_task_id: str | None = None) -> list[dict[str, Any]]:
        if created_by_task_id:
            rows = self.conn.execute(
                "SELECT * FROM artifacts WHERE created_by_task_id = ?",
                (created_by_task_id,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM artifacts").fetchall()
        return [dict(r) for r in rows]

    def get_artifact(self, sha256: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM artifacts WHERE sha256 = ?", (sha256,)).fetchone()
        return dict(row) if row else None

    @_synchronized
    def record_validator_results(
        self, *, run_id: str, task_id: str | None, results: list[dict[str, Any]]
    ) -> None:
        for result in results:
            self.conn.execute(
                """
                INSERT INTO validator_results (run_id, task_id, result_json)
                VALUES (?, ?, ?)
                """,
                (run_id, task_id, json.dumps(result, default=str)),
            )
        self.conn.commit()

    def list_validator_results(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM validator_results WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def append_event(self, event: ObservabilityEvent) -> int:
        now = datetime.now(UTC).isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO events
            (event_id, occurred_at, recorded_at, event_type, schema_version,
             run_id, task_id, request_id, tool_call_id, trace_id, span_id,
             parent_span_id, severity, summary, payload_json, content_refs_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.occurred_at.isoformat(),
                now,
                event.type,
                event.schema_version,
                event.run_id,
                event.task_id,
                event.request_id,
                event.tool_call_id,
                event.trace_id,
                event.span_id,
                event.parent_span_id,
                event.severity.value if hasattr(event.severity, "value") else str(event.severity),
                event.summary,
                json.dumps(event.payload, default=str),
                json.dumps([c.model_dump(mode="json") for c in event.content_refs]),
            ),
        )
        self.conn.execute(
            """
            UPDATE runs SET last_progress_at=?, updated_at=?
            WHERE run_id=?
            """,
            (now, now, event.run_id),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def latest_seq(self) -> int:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()
        return int(row["m"] if row else 0)

    def latest_seq_for_run(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["m"] if row else 0)

    def count_error_events(self, run_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM events WHERE run_id = ? AND severity = 'error'",
            (run_id,),
        ).fetchone()
        return int(row["c"] if row else 0)

    def list_events(
        self,
        *,
        run_id: str | None = None,
        after_seq: int = 0,
        limit: int = 200,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["seq > ?"]
        params: list[Any] = [after_seq]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if types:
            placeholders = ",".join("?" for _ in types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(types)
        params.append(limit)
        sql = f"""
            SELECT * FROM events
            WHERE {" AND ".join(clauses)}
            ORDER BY seq ASC
            LIMIT ?
        """
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def last_event_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT recorded_at FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["recorded_at"] if row else None

    def cache_model_catalog(self, payload: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO model_catalog_cache (id, payload_json, refreshed_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              payload_json=excluded.payload_json,
              refreshed_at=excluded.refreshed_at
            """,
            (json.dumps(payload), datetime.now(UTC).isoformat()),
        )
        self.conn.commit()

    def get_model_catalog(self) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json, refreshed_at FROM model_catalog_cache WHERE id = 1"
        ).fetchone()
        if not row:
            return None
        return {"payload": json.loads(row["payload_json"]), "refreshed_at": row["refreshed_at"]}
