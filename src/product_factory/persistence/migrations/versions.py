"""Ordered migration definitions for Product Factory SQLite."""

from __future__ import annotations

import sqlite3

from product_factory.persistence.migrations.runner import Migration


def _upgrade_001_baseline(conn: sqlite3.Connection) -> None:
    """Create the pre-SD0 schema on an empty database."""
    from product_factory.persistence.database import SCHEMA_SQL, _ensure_column

    conn.executescript(SCHEMA_SQL)
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
    _ensure_column(conn, "model_invocations", "routing_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "tool_calls", "status", "TEXT NOT NULL DEFAULT 'completed'")
    _ensure_column(conn, "tool_calls", "started_at", "TEXT")
    _ensure_column(conn, "tool_calls", "ended_at", "TEXT")
    _ensure_column(conn, "tasks", "effective_policy_json", "TEXT")


_BASELINE_SOURCE = "sd0.a:001:pre_sd0_schema_sql+ensure_columns"


def _upgrade_002_rename_legacy_approvals(conn: sqlite3.Connection) -> None:
    """Rename unused approvals table; preserve rows; never treat as authority."""
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    if "legacy_approvals" in tables:
        return
    if "approvals" not in tables:
        # Empty path after a future schema that never created approvals.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS legacy_approvals (
                approval_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                decided_at TEXT
            )
            """
        )
        return
    conn.execute("ALTER TABLE approvals RENAME TO legacy_approvals")


_RENAME_SOURCE = "sd0.a:002:rename_approvals_to_legacy_approvals"


def _upgrade_003_handoff_records(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS handoff_records (
            handoff_id TEXT PRIMARY KEY,
            producer_artifact_instance_id TEXT NOT NULL,
            producer_run_id TEXT NOT NULL,
            producer_task_id TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            schema_id TEXT NOT NULL,
            schema_version TEXT,
            role TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            superseded_by TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (producer_run_id) REFERENCES runs(run_id),
            FOREIGN KEY (producer_artifact_instance_id) REFERENCES artifact_instances(instance_id)
        );
        CREATE INDEX IF NOT EXISTS handoff_records_producer_run
            ON handoff_records(producer_run_id);
        CREATE INDEX IF NOT EXISTS handoff_records_sha
            ON handoff_records(sha256);

        CREATE TABLE IF NOT EXISTS handoff_consumptions (
            consumption_id INTEGER PRIMARY KEY AUTOINCREMENT,
            consumer_run_id TEXT NOT NULL,
            handoff_id TEXT NOT NULL,
            producer_artifact_instance_id TEXT NOT NULL,
            consumer_artifact_instance_id TEXT NOT NULL,
            state_at_resolution TEXT NOT NULL,
            resolved_at TEXT NOT NULL,
            UNIQUE (consumer_run_id, handoff_id),
            FOREIGN KEY (consumer_run_id) REFERENCES runs(run_id),
            FOREIGN KEY (handoff_id) REFERENCES handoff_records(handoff_id)
        );
        CREATE INDEX IF NOT EXISTS handoff_consumptions_handoff
            ON handoff_consumptions(handoff_id);
        """
    )


_HANDOFF_SOURCE = "sd0.b:003:handoff_records_and_consumptions"


def _upgrade_004_action_approvals(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS action_approvals (
            approval_id TEXT PRIMARY KEY,
            action_type TEXT NOT NULL,
            subject_run_id TEXT NOT NULL,
            subject_artifact_instance_id TEXT,
            action_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            actor_json TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            expires_at TEXT,
            consumed_at TEXT,
            consumed_by_run_id TEXT,
            reconciliation_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (subject_run_id) REFERENCES runs(run_id)
        );
        CREATE INDEX IF NOT EXISTS action_approvals_fingerprint
            ON action_approvals(action_fingerprint);
        CREATE INDEX IF NOT EXISTS action_approvals_subject_run
            ON action_approvals(subject_run_id);
        """
    )


_ACTION_APPROVAL_SOURCE = "sd0.c:004:action_approvals"


def _upgrade_005_evaluation_schema(conn: sqlite3.Connection) -> None:
    """Move evaluation DDL into the versioned migration ledger (SD3.A)."""
    from product_factory.persistence.database import _ensure_column

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS evaluation_cases (
            case_id TEXT PRIMARY KEY,
            suite TEXT NOT NULL,
            case_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluation_runs (
            eval_run_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            config_name TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluation_scores (
            score_id TEXT PRIMARY KEY,
            bench_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            seed INTEGER NOT NULL DEFAULT 0,
            score_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluation_benches (
            bench_id TEXT PRIMARY KEY,
            report_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS evaluation_pairwise (
            pair_id TEXT PRIMARY KEY,
            bench_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            seed INTEGER NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    _ensure_column(conn, "evaluation_scores", "seed", "INTEGER NOT NULL DEFAULT 0")


_EVAL_SOURCE = "sd3.a:005:evaluation_aggregate_schema"


def _upgrade_006_retention_and_maintenance(conn: sqlite3.Connection) -> None:
    """Pin registry and append-only maintenance audit (SD3.D)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS retention_pins (
            pin_id TEXT PRIMARY KEY,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            pinned_at TEXT NOT NULL,
            pinned_by TEXT NOT NULL DEFAULT 'operator',
            UNIQUE (target_kind, target_id)
        );
        CREATE INDEX IF NOT EXISTS retention_pins_target
            ON retention_pins(target_kind, target_id);

        CREATE TABLE IF NOT EXISTS maintenance_audit (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            action TEXT NOT NULL,
            dry_run INTEGER NOT NULL DEFAULT 1,
            actor TEXT NOT NULL DEFAULT 'operator',
            payload_json TEXT NOT NULL,
            backup_ref TEXT
        );
        """
    )


_RETENTION_SOURCE = "sd3.d:006:retention_pins_and_maintenance_audit"


def _upgrade_007_effective_policy_v2(conn: sqlite3.Connection) -> None:
    """Add durable provenance used to fail closed before a resume spends."""
    from product_factory.persistence.database import _ensure_column

    _ensure_column(conn, "runs", "workflow_pack_digest", "TEXT")
    _ensure_column(conn, "runs", "blocked_reason_json", "TEXT")
    _ensure_column(conn, "tasks", "effective_policy_schema", "TEXT")
    _ensure_column(conn, "tasks", "effective_policy_digest", "TEXT")


_EFFECTIVE_POLICY_V2_SOURCE = "r1:007:effective_policy_v2_provenance"


def _upgrade_008_transactional_lifecycle(conn: sqlite3.Connection) -> None:
    """Add attempt, reservation, lineage, and external-intent recovery records."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            admitted_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            recovery_state TEXT,
            provider_request_id TEXT,
            tool_receipts_json TEXT NOT NULL DEFAULT '[]',
            result_json TEXT,
            UNIQUE(run_id, task_id, attempt_number),
            FOREIGN KEY(run_id, task_id) REFERENCES tasks(run_id, task_id)
        );
        CREATE INDEX IF NOT EXISTS task_attempts_recovery
        ON task_attempts(state, recovery_state);

        CREATE TABLE IF NOT EXISTS budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            attempt_id TEXT NOT NULL UNIQUE,
            reserved_cost_usd TEXT NOT NULL,
            settled_cost_usd TEXT,
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            settled_at TEXT,
            FOREIGN KEY(run_id, task_id) REFERENCES tasks(run_id, task_id),
            FOREIGN KEY(attempt_id) REFERENCES task_attempts(attempt_id)
        );

        CREATE TABLE IF NOT EXISTS task_lineage (
            lineage_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            origin_task_id TEXT,
            supersedes_task_id TEXT,
            inherited_patch_fingerprint TEXT,
            lineage_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            UNIQUE(run_id, task_id),
            FOREIGN KEY(run_id, task_id) REFERENCES tasks(run_id, task_id)
        );

        CREATE TABLE IF NOT EXISTS external_action_intents (
            intent_id TEXT PRIMARY KEY,
            approval_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            action_fingerprint TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            state TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            reconciliation_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            reconciled_at TEXT,
            UNIQUE(action_fingerprint, idempotency_key),
            FOREIGN KEY(approval_id) REFERENCES action_approvals(approval_id),
            FOREIGN KEY(run_id) REFERENCES runs(run_id)
        );
        """
    )


_TRANSACTIONAL_LIFECYCLE_SOURCE = "r4:008:attempt_budget_lineage_action_intent"


MIGRATIONS: list[Migration] = [
    Migration(1, "baseline_pre_sd0_schema", _upgrade_001_baseline, source=_BASELINE_SOURCE),
    Migration(
        2,
        "rename_approvals_to_legacy_approvals",
        _upgrade_002_rename_legacy_approvals,
        source=_RENAME_SOURCE,
    ),
    Migration(3, "handoff_records", _upgrade_003_handoff_records, source=_HANDOFF_SOURCE),
    Migration(4, "action_approvals", _upgrade_004_action_approvals, source=_ACTION_APPROVAL_SOURCE),
    Migration(
        5, "evaluation_aggregate_schema", _upgrade_005_evaluation_schema, source=_EVAL_SOURCE
    ),
    Migration(
        6,
        "retention_pins_and_maintenance_audit",
        _upgrade_006_retention_and_maintenance,
        source=_RETENTION_SOURCE,
    ),
    Migration(
        7,
        "effective_policy_v2_provenance",
        _upgrade_007_effective_policy_v2,
        source=_EFFECTIVE_POLICY_V2_SOURCE,
    ),
    Migration(
        8,
        "transactional_lifecycle_records",
        _upgrade_008_transactional_lifecycle,
        source=_TRANSACTIONAL_LIFECYCLE_SOURCE,
    ),
]
