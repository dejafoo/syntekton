"""SR3.A unit-of-work atomicity and foreign-key enforcement tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from product_factory.observability.contracts import EventSeverity, ObservabilityEvent
from product_factory.persistence.database import Database
from product_factory.persistence.unit_of_work import UnitOfWork


def _event(
    *,
    run_id: str,
    event_type: str,
    task_id: str | None = None,
    summary: str = "",
) -> ObservabilityEvent:
    return ObservabilityEvent(
        event_id=str(uuid4()),
        type=event_type,
        run_id=run_id,
        task_id=task_id,
        severity=EventSeverity.INFO,
        summary=summary or event_type,
        payload={},
    )


def test_foreign_keys_enabled_on_actor_and_immediate(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    assert int(db.conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    with db._actor.immediate() as conn:
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        with pytest.raises(Exception):  # noqa: B017 — SQLite FK failure type varies by binding
            conn.execute(
                "INSERT INTO tasks(run_id, task_id, capability, status, spec_json) "
                "VALUES ('missing', 't1', 'c', 'pending', '{}')"
            )
    db.close()


def test_admit_run_with_events_commits_atomically(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    uow = db.unit_of_work()
    run_id = "run-admit-ok"
    seqs = uow.admit_run_with_events(
        run_id=run_id,
        workflow_type="code_change",
        status="queued",
        request={"goal": "x"},
        events=[
            _event(run_id=run_id, event_type="run.admitted", summary="admitted"),
            _event(run_id=run_id, event_type="run.queued", summary="queued"),
        ],
    )
    assert len(seqs) == 2
    assert db.get_run(run_id) is not None
    assert db.get_run(run_id)["status"] == "queued"
    events = db.list_events(run_id=run_id)
    assert [e["event_type"] for e in events] == ["run.admitted", "run.queued"]
    db.close()


def test_admit_run_with_events_rolls_back_on_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    uow = db.unit_of_work()
    run_id = "run-admit-fail"
    calls = {"n": 0}
    real_append = db.events.append_event

    def boom(event: ObservabilityEvent) -> int:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("injected event write failure")
        return real_append(event)

    monkeypatch.setattr(db.events, "append_event", boom)
    with pytest.raises(RuntimeError, match="injected event write failure"):
        uow.admit_run_with_events(
            run_id=run_id,
            workflow_type="code_change",
            status="queued",
            request={"goal": "x"},
            events=[
                _event(run_id=run_id, event_type="run.admitted"),
                _event(run_id=run_id, event_type="run.queued"),
            ],
        )
    assert db.get_run(run_id) is None
    assert db.list_events(run_id=run_id) == []
    assert not db._actor.in_transaction
    db.close()


def test_complete_task_with_event_commits_atomically(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    run_id = "run-task-ok"
    db.upsert_run(run_id=run_id, workflow_type="code_change", status="running", request={})
    db.upsert_task(
        run_id=run_id,
        task_id="t1",
        capability="implementation",
        status="running",
        spec={"dependencies": []},
    )
    uow = db.unit_of_work()
    seq = uow.complete_task_with_event(
        run_id=run_id,
        task_id="t1",
        capability="implementation",
        status="success",
        spec={"dependencies": []},
        result={"ok": True},
        ended_at="2026-08-08T00:00:00+00:00",
        event=_event(
            run_id=run_id,
            task_id="t1",
            event_type="task.completed",
            summary="done",
        ),
    )
    assert seq > 0
    task = db.get_task(run_id, "t1")
    assert task is not None
    assert task["status"] == "success"
    events = db.list_events(run_id=run_id, types=["task.completed"])
    assert len(events) == 1
    assert events[0]["task_id"] == "t1"
    db.close()


def test_complete_task_with_event_rolls_back_on_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    run_id = "run-task-fail"
    db.upsert_run(run_id=run_id, workflow_type="code_change", status="running", request={})
    db.upsert_task(
        run_id=run_id,
        task_id="t1",
        capability="implementation",
        status="running",
        spec={"dependencies": []},
    )
    uow = db.unit_of_work()

    def boom(_event: ObservabilityEvent) -> int:
        raise RuntimeError("injected after task write")

    monkeypatch.setattr(db.events, "append_event", boom)
    with pytest.raises(RuntimeError, match="injected after task write"):
        uow.complete_task_with_event(
            run_id=run_id,
            task_id="t1",
            capability="implementation",
            status="success",
            spec={"dependencies": []},
            result={"ok": True},
            event=_event(run_id=run_id, task_id="t1", event_type="task.completed"),
        )
    task = db.get_task(run_id, "t1")
    assert task is not None
    assert task["status"] == "running"
    assert db.list_events(run_id=run_id, types=["task.completed"]) == []
    assert not db._actor.in_transaction
    db.close()


def test_begin_defers_repository_commits_until_exit(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    uow = db.unit_of_work()
    run_id = "run-begin"
    with uow.begin():
        assert db._actor.in_transaction
        db.upsert_run(
            run_id=run_id,
            workflow_type="code_change",
            status="queued",
            request={"goal": "y"},
        )
        # Uncommitted to other connections until UoW exits.
        probe = db._actor.thread_local_connection()
        try:
            row = probe.execute("SELECT run_id FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            assert row is None
        finally:
            probe.close()
        db.append_event(_event(run_id=run_id, event_type="run.admitted"))
    assert db.get_run(run_id) is not None
    assert len(db.list_events(run_id=run_id)) == 1
    db.close()


def test_begin_rolls_back_coupled_writes_on_fault(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    uow = db.unit_of_work()
    run_id = "run-begin-fail"
    with pytest.raises(RuntimeError, match="injected mid-uow"), uow.begin():
        db.upsert_run(
            run_id=run_id,
            workflow_type="code_change",
            status="queued",
            request={},
        )
        db.append_event(_event(run_id=run_id, event_type="run.admitted"))
        raise RuntimeError("injected mid-uow")
    assert db.get_run(run_id) is None
    assert db.list_events(run_id=run_id) == []
    db.close()


def test_unit_of_work_from_database_exposes_aggregates(tmp_path: Path) -> None:
    db = Database(tmp_path / "sr3.sqlite")
    uow = UnitOfWork.from_database(db)
    assert uow.runs is db.runs
    assert uow.tasks is db.tasks
    assert uow.events is db.events
    assert isinstance(db.unit_of_work(), UnitOfWork)
    db.close()


def test_autonomous_repository_writes_still_commit(tmp_path: Path) -> None:
    """Outside a UoW, repository methods remain immediately durable."""
    db = Database(tmp_path / "sr3.sqlite")
    db.upsert_run(run_id="r-auto", workflow_type="code_change", status="queued", request={})
    probe = db._actor.thread_local_connection()
    try:
        row = probe.execute("SELECT status FROM runs WHERE run_id = 'r-auto'").fetchone()
        assert row is not None
        assert row["status"] == "queued"
    finally:
        probe.close()
    db.close()
