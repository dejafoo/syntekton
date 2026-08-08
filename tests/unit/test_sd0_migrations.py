"""SD0.A versioned migration runner tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from product_factory.persistence.database import SCHEMA_SQL, Database, connect, migrate
from product_factory.persistence.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    apply_migrations,
)
from product_factory.persistence.migrations.runner import PRE_SD0_REQUIRED_TABLES


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }


def _create_pre_sd0_db(path: Path) -> None:
    """Full post-RF6 / pre-migration-runner schema including unused approvals."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)
    conn.execute(
        "INSERT INTO approvals(approval_id, run_id, status, payload_json, decided_at) "
        "VALUES ('legacy-1', 'run-x', 'pending', '{}', NULL)"
    )
    conn.execute(
        """
        INSERT INTO runs(run_id, workflow_type, status, request_json, created_at, updated_at, usage_json)
        VALUES ('run-x', 'code_change', 'succeeded', '{}', '2024-01-01T00:00:00Z',
                '2024-01-01T00:00:00Z', '{}')
        """
    )
    conn.commit()
    conn.close()


def test_empty_database_applies_all_migrations_in_order(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.sqlite"
    db = Database(db_path)
    versions = [
        int(row["version"])
        for row in db.conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    ]
    assert versions == [m.version for m in MIGRATIONS]
    tables = _table_names(db.conn)
    assert "schema_migrations" in tables
    assert "legacy_approvals" in tables
    assert "approvals" not in tables
    assert "handoff_records" in tables
    assert "handoff_consumptions" in tables
    assert "action_approvals" in tables
    fk = db.conn.execute("PRAGMA foreign_keys").fetchone()
    assert int(fk[0]) == 1
    db.close()


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "idemp.sqlite"
    first = Database(db_path)
    first.close()
    conn = connect(db_path)
    newly = apply_migrations(conn)
    assert newly == []
    rows = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()
    assert int(rows[0]) == len(MIGRATIONS)
    conn.close()


def test_pre_sd0_upgrade_baselines_and_renames_approvals(tmp_path: Path) -> None:
    db_path = tmp_path / "pre_sd0.sqlite"
    _create_pre_sd0_db(db_path)
    assert "approvals" in _table_names(sqlite3.connect(db_path))
    db = Database(db_path)
    tables = _table_names(db.conn)
    assert "legacy_approvals" in tables
    assert "approvals" not in tables
    row = db.conn.execute(
        "SELECT approval_id, status FROM legacy_approvals WHERE approval_id='legacy-1'"
    ).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    run = db.conn.execute("SELECT status FROM runs WHERE run_id='run-x'").fetchone()
    assert run["status"] == "succeeded"
    db.close()


def test_partial_legacy_schema_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "partial.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            workflow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    with pytest.raises(MigrationError, match="partial legacy schema|Unsupported"):
        Database(db_path)


def test_checksum_drift_is_refused(tmp_path: Path) -> None:
    db_path = tmp_path / "drift.sqlite"
    Database(db_path).close()
    conn = connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "UPDATE schema_migrations SET checksum=? WHERE version=1",
        ("0" * 64,),
    )
    conn.commit()
    with pytest.raises(MigrationError, match="Checksum drift"):
        apply_migrations(conn)
    conn.close()


def test_migration_ordering_rejects_out_of_order_plan(tmp_path: Path) -> None:
    db_path = tmp_path / "order.sqlite"
    conn = connect(db_path)

    def noop(_conn: sqlite3.Connection) -> None:
        return None

    bad = [
        Migration(2, "b", noop, source="b"),
        Migration(1, "a", noop, source="a"),
    ]
    with pytest.raises(MigrationError, match="ordered"):
        apply_migrations(conn, migrations=bad)
    conn.close()


def test_foreign_keys_enforced_on_handoff_fk(tmp_path: Path) -> None:
    db = Database(tmp_path / "fk.sqlite")
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            """
            INSERT INTO handoff_records(
                handoff_id, producer_artifact_instance_id, producer_run_id,
                producer_task_id, sha256, schema_id, role, state, created_at, updated_at
            ) VALUES (
                'h1', 'missing-instance', 'missing-run', 't0', ?,
                'release_plan.v1', 'release_plan', 'draft',
                '2024-01-01T00:00:00Z', '2024-01-01T00:00:00Z'
            )
            """,
            ("a" * 64,),
        )
        db.conn.commit()
    db.close()


def test_pre_sd0_required_tables_match_schema_sql() -> None:
    import re

    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", SCHEMA_SQL))
    assert tables == set(PRE_SD0_REQUIRED_TABLES)


def test_migrate_function_delegates(tmp_path: Path) -> None:
    conn = connect(tmp_path / "deleg.sqlite")
    migrate(conn)
    assert "schema_migrations" in _table_names(conn)
    conn.close()
