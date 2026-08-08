"""Versioned SQLite migration runner (SD0.A)."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime


class MigrationError(RuntimeError):
    """Raised when schema migration cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    upgrade: Callable[[sqlite3.Connection], None]
    source: str

    @property
    def checksum(self) -> str:
        body = f"{self.version}:{self.name}:{self.source}"
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""

# Tables that define a supported pre-SD0 (post-RF6) database before the ledger exists.
PRE_SD0_REQUIRED_TABLES: frozenset[str] = frozenset(
    {
        "runs",
        "tasks",
        "worker_leases",
        "task_dependencies",
        "model_invocations",
        "tool_calls",
        "artifacts",
        "artifact_instances",
        "findings",
        "validator_results",
        "approvals",
        "model_catalog_cache",
        "evaluation_runs",
        "events",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {str(row[0] if not isinstance(row, sqlite3.Row) else row["name"]) for row in rows}


def _ensure_foreign_keys(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    enabled = conn.execute("PRAGMA foreign_keys").fetchone()
    if enabled is None or int(enabled[0]) != 1:
        raise MigrationError("PRAGMA foreign_keys must be ON for every connection")


def _applied_rows(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    if "schema_migrations" not in _table_names(conn):
        return {}
    rows = conn.execute(
        "SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {int(row["version"]): row for row in rows}


def _record_applied(conn: sqlite3.Connection, migration: Migration) -> None:
    conn.execute(
        """
        INSERT INTO schema_migrations(version, name, checksum, applied_at)
        VALUES (?, ?, ?, ?)
        """,
        (migration.version, migration.name, migration.checksum, _utc_now()),
    )


def _baseline_existing_database(conn: sqlite3.Connection, baseline: Migration) -> None:
    """Record baseline for a supported pre-SD0 DB without rebuilding tables."""
    tables = _table_names(conn)
    if "schema_migrations" in tables:
        return
    if not PRE_SD0_REQUIRED_TABLES.issubset(tables):
        missing = sorted(PRE_SD0_REQUIRED_TABLES - tables)
        raise MigrationError(
            "Unsupported or partial legacy schema; back up the data directory and "
            "upgrade from a supported pre-SD0 snapshot (or start from an empty database). "
            f"Missing required tables: {missing}"
        )
    unexpected = tables - PRE_SD0_REQUIRED_TABLES - {"schema_migrations"}
    # Allow SQLite auto artifacts; refuse unknown product tables.
    product_unexpected = {name for name in unexpected if not name.startswith("sqlite_")}
    # evaluation_* extras from EvalStore are tolerated as additive side tables.
    tolerated = {name for name in product_unexpected if name.startswith("evaluation_")}
    unknown = product_unexpected - tolerated
    if unknown:
        raise MigrationError(
            "Unknown legacy schema objects present; back up and migrate manually. "
            f"Unexpected tables: {sorted(unknown)}"
        )
    conn.executescript(SCHEMA_MIGRATIONS_DDL)
    _record_applied(conn, baseline)


def apply_migrations(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] | None = None,
) -> list[int]:
    """Apply pending migrations transactionally. Returns newly applied versions."""
    from product_factory.persistence.migrations.versions import MIGRATIONS

    _ensure_foreign_keys(conn)
    plan = list(migrations if migrations is not None else MIGRATIONS)
    if not plan:
        return []
    if sorted(m.version for m in plan) != [m.version for m in plan]:
        raise MigrationError("Migration plan must be strictly ordered by version")
    versions = [m.version for m in plan]
    if versions != list(range(versions[0], versions[0] + len(versions))):
        raise MigrationError("Migration versions must be contiguous integers")

    baseline = plan[0]
    tables = _table_names(conn)
    applied = _applied_rows(conn)

    if not applied and tables:
        # Existing DB without ledger: only auto-baseline when it matches pre-SD0.
        _baseline_existing_database(conn, baseline)
        conn.commit()
        applied = _applied_rows(conn)

    if not applied and not tables:
        conn.executescript(SCHEMA_MIGRATIONS_DDL)
        conn.commit()
        applied = {}

    # Drift / ordering checks for already-recorded versions.
    for migration in plan:
        row = applied.get(migration.version)
        if row is None:
            continue
        if str(row["checksum"]) != migration.checksum:
            raise MigrationError(
                f"Checksum drift for migration {migration.version} ({migration.name}): "
                f"applied={row['checksum']} current={migration.checksum}"
            )
        if str(row["name"]) != migration.name:
            raise MigrationError(
                f"Name drift for migration {migration.version}: "
                f"applied={row['name']!r} current={migration.name!r}"
            )

    newly: list[int] = []
    for migration in plan:
        if migration.version in applied:
            continue
        # Refuse holes: previous version must exist unless this is the first.
        if migration.version > baseline.version and (migration.version - 1) not in {
            *applied,
            *newly,
        }:
            raise MigrationError(f"Cannot apply migration {migration.version}; missing predecessor")
        try:
            migration.upgrade(conn)
            _record_applied(conn, migration)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        newly.append(migration.version)
        applied[migration.version] = None  # type: ignore[assignment]
    return newly
