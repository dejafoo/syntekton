"""Serialized SQLite connection actor (SD3.A).

All durable access goes through SqliteActor. The actor owns one connection and
an RLock (explicit serialized database actor). Foreign keys are verified on
connect and before every transaction. Connection-per-thread is available via
thread_local helpers for read-only probes; writers must use the actor.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


class SqliteConnectionError(RuntimeError):
    """Raised when a SQLite connection cannot satisfy durability policy."""


def ensure_foreign_keys(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    enabled = conn.execute("PRAGMA foreign_keys").fetchone()
    if enabled is None or int(enabled[0]) != 1:
        raise SqliteConnectionError("PRAGMA foreign_keys must be ON for every connection")


def connect(db_path: Path, *, check_same_thread: bool = False) -> sqlite3.Connection:
    """Open a SQLite connection with durability pragmas and FK enforcement."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    ensure_foreign_keys(conn)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


class SqliteActor:
    """Serialized database actor: one connection, explicit lock, FK-verified.

    Transaction policy
    ------------------
    - Use ``immediate()`` for multi-statement mutations (run/task/event/budget/
      lease transitions). Rollback on exception.
    - Single-statement writes may use ``run()`` which holds the actor lock;
      callers commit explicitly after successful mutation.
    - Lock/retry: SQLite busy_timeout=5000; treat ``sqlite3.OperationalError``
      ("database is locked") as retryable at the service boundary when not
      already inside an immediate transaction.
    """

    def __init__(self, db_path: Path, *, migrate: bool = True) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn = connect(self.db_path, check_same_thread=False)
        self._closed = False
        if migrate:
            from product_factory.persistence.migrations import apply_migrations

            apply_migrations(self._conn)

    @property
    def connection(self) -> sqlite3.Connection:
        """Persistence-internal connection. Do not use from evaluation/app layers."""
        if self._closed:
            raise SqliteConnectionError("SqliteActor connection is closed")
        return self._conn

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def wal_enabled(self) -> bool:
        def _check(conn: sqlite3.Connection) -> bool:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            return bool(row and str(row[0]).lower() == "wal")

        return self.run(_check)

    def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Execute ``fn(conn)`` under the actor lock; verify FKs first."""
        with self._lock:
            if self._closed:
                raise SqliteConnectionError("SqliteActor connection is closed")
            ensure_foreign_keys(self._conn)
            return fn(self._conn)

    @contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE transaction under the actor lock."""
        with self._lock:
            if self._closed:
                raise SqliteConnectionError("SqliteActor connection is closed")
            ensure_foreign_keys(self._conn)
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def thread_local_connection(self) -> sqlite3.Connection:
        """Open an additional FK-enforced connection (read-mostly / isolation tests)."""
        return connect(self.db_path, check_same_thread=True)


_thread_local = threading.local()


def get_thread_connection(db_path: Path) -> sqlite3.Connection:
    """Connection-per-thread helper for callers that opt into thread-local reads."""
    key = str(db_path.resolve())
    cache: dict[str, sqlite3.Connection] = getattr(_thread_local, "connections", {})
    conn = cache.get(key)
    if conn is None:
        conn = connect(db_path, check_same_thread=True)
        cache[key] = conn
        _thread_local.connections = cache
    else:
        ensure_foreign_keys(conn)
    return conn
