"""SD3.A repository / connection / FK / evaluation boundary tests."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from product_factory.evaluation.store import EvalStore
from product_factory.persistence.connection import (
    SqliteActor,
    connect,
    ensure_foreign_keys,
    get_thread_connection,
)
from product_factory.persistence.database import Database
from product_factory.persistence.migrations import MIGRATIONS


def test_sqlite_actor_enforces_foreign_keys(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    ensure_foreign_keys(db.conn)
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute(
            "INSERT INTO tasks(run_id, task_id, capability, status, spec_json) "
            "VALUES ('missing', 't1', 'c', 'pending', '{}')"
        )
        db.conn.commit()
    db.conn.rollback()
    db.close()


def test_thread_local_connection_also_enforces_fk(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite"
    Database(path).close()
    conn = get_thread_connection(path)
    ensure_foreign_keys(conn)
    enabled = conn.execute("PRAGMA foreign_keys").fetchone()
    assert int(enabled[0]) == 1
    conn.close()


def test_connection_isolation_across_threads(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    db.upsert_run(run_id="r1", workflow_type="code_change", status="queued", request={"x": 1})
    errors: list[BaseException] = []

    def writer(i: int) -> None:
        try:
            db.upsert_task(
                run_id="r1",
                task_id=f"t{i}",
                capability="implementation",
                status="pending",
                spec={"dependencies": []},
            )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(db.list_tasks("r1")) == 8
    db.close()


def test_eval_store_does_not_use_db_conn(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    store = EvalStore(db)
    assert not hasattr(store, "conn")
    store.upsert_case("case-1", "suite", {"title": "x"})
    # Reach through repository, not db.conn
    row = db.evaluations.get_bench("missing")
    assert row is None
    # Dual-write compatibility path still lands evaluation_runs via repository.
    from product_factory.evaluation.deterministic import EvaluationScore

    score = EvaluationScore(
        case_id="case-1",
        subject_id="local",
        seed=0,
        deterministic_pass=True,
    )
    store.record_score(bench_id="b1", score=score)
    assert store.list_scores("b1")
    runs = db.conn.execute("SELECT COUNT(*) AS c FROM evaluation_runs").fetchone()
    assert int(runs["c"]) >= 1
    db.close()


def test_migrations_include_eval_and_retention(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    versions = [
        int(r["version"])
        for r in db.conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    ]
    assert versions == [m.version for m in MIGRATIONS]
    tables = {
        r[0]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "evaluation_scores" in tables
    assert "retention_pins" in tables
    assert "maintenance_audit" in tables
    db.close()


def test_aggregate_repositories_exposed(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    assert db.runs is not None
    assert db.tasks is not None
    assert db.workers is not None
    assert db.artifact_meta is not None
    assert db.handoffs is not None
    assert db.approvals is not None
    assert db.events is not None
    assert db.evaluations is not None
    assert isinstance(db._actor, SqliteActor)
    db.close()


def test_connect_helper_sets_fk(tmp_path: Path) -> None:
    conn = connect(tmp_path / "raw.sqlite")
    assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    conn.close()
