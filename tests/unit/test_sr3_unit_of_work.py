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


def test_wave_execution_production_path_uses_unit_of_work() -> None:
    """AST guard: WaveExecutionService + engine call the SR3 completion helpers."""
    import ast

    root = Path(__file__).resolve().parents[2]
    wave_path = root / "src" / "product_factory" / "orchestration" / "wave_execution.py"
    engine_path = root / "src" / "product_factory" / "orchestration" / "lifecycle" / "engine.py"
    host_path = root / "src" / "product_factory" / "host" / "service.py"
    wave_source = wave_path.read_text(encoding="utf-8")
    engine_source = engine_path.read_text(encoding="utf-8")
    host_source = host_path.read_text(encoding="utf-8")

    assert "unit_of_work" in wave_source
    assert "complete_task_with_event" in wave_source
    assert "record_task_completion" in wave_source
    assert ".record_task_completion(" not in engine_source
    assert "admit_run_with_events" in host_source

    wave_tree = ast.parse(wave_source)
    saw_uow = False
    saw_complete = False
    for node in ast.walk(wave_tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "unit_of_work":
                    saw_uow = True
                if func.attr == "complete_task_with_event":
                    saw_complete = True
    assert saw_uow, "wave_execution.py must call db.unit_of_work()"
    assert saw_complete, "wave_execution.py must call complete_task_with_event"

    host_tree = ast.parse(host_source)
    saw_admit = False
    for node in ast.walk(host_tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "admit_run_with_events":
                saw_admit = True
    assert saw_admit, "host/service.py must call admit_run_with_events"


def test_wave_execution_record_task_completion_uses_uow(tmp_path: Path) -> None:
    """Production helper couples terminal task upsert with its event."""
    from product_factory.domain.tasks import TaskResult, TaskSpec
    from product_factory.orchestration.wave_execution import WaveExecutionService

    db = Database(tmp_path / "sr3.sqlite")
    run_id = "run-wave-uow"
    db.upsert_run(run_id=run_id, workflow_type="code_change", status="running", request={})
    task = TaskSpec(
        id="t1",
        title="Implement",
        capability="implementation",
        objective="do the thing",
        expected_output_schema="artifact_ref",
        dependencies=[],
    )
    result = TaskResult(task_id="t1", status="success", summary="done")
    seq = WaveExecutionService.record_task_completion(
        db=db,
        run_id=run_id,
        task=task,
        result=result,
    )
    assert seq > 0
    row = db.get_task(run_id, "t1")
    assert row is not None
    assert row["status"] == "success"
    events = db.list_events(run_id=run_id, types=["task.completed"])
    assert len(events) == 1
    assert events[0]["task_id"] == "t1"
    db.close()


def test_attempt_and_budget_settlement_is_atomic(tmp_path: Path) -> None:
    db = Database(tmp_path / "r4.sqlite")
    run_id = "run-attempt"
    task_id = "task-1"
    db.upsert_run(run_id=run_id, workflow_type="repository_change", status="executing", request={})
    uow = db.unit_of_work()
    uow.start_task_with_event(
        run_id=run_id,
        task_id=task_id,
        capability="implementation",
        spec={"dependencies": []},
        attempt_id="attempt-1",
        attempt_number=1,
        idempotency_key="run-attempt:task-1:1",
        reserved_cost_usd="1.00",
        event=_event(run_id=run_id, task_id=task_id, event_type="task.started"),
    )
    uow.settle_task_attempt_with_event(
        run_id=run_id,
        task_id=task_id,
        capability="implementation",
        spec={"dependencies": []},
        result={"status": "success", "summary": "done"},
        attempt_id="attempt-1",
        settled_cost_usd="0.25",
        tool_receipts=[{"tool_call_id": "tc-1"}],
        event=_event(run_id=run_id, task_id=task_id, event_type="task.completed"),
    )
    attempt = db.conn.execute(
        "SELECT state, tool_receipts_json FROM task_attempts WHERE attempt_id='attempt-1'"
    ).fetchone()
    reservation = db.conn.execute(
        "SELECT state, settled_cost_usd FROM budget_reservations WHERE attempt_id='attempt-1'"
    ).fetchone()
    assert dict(attempt)["state"] == "completed"
    assert dict(reservation) == {"state": "settled", "settled_cost_usd": "0.25"}
    assert [row["event_type"] for row in db.list_events(run_id=run_id)] == [
        "task.started",
        "task.completed",
    ]
    db.close()


def test_attempt_settlement_rolls_back_after_event_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "r4-fault.sqlite")
    run_id = "run-attempt-fault"
    task_id = "task-1"
    db.upsert_run(run_id=run_id, workflow_type="repository_change", status="executing", request={})
    uow = db.unit_of_work()
    uow.start_task_with_event(
        run_id=run_id,
        task_id=task_id,
        capability="implementation",
        spec={"dependencies": []},
        attempt_id="attempt-fault",
        attempt_number=1,
        idempotency_key="run-attempt-fault:task-1:1",
        reserved_cost_usd="1.00",
        event=_event(run_id=run_id, task_id=task_id, event_type="task.started"),
    )
    monkeypatch.setattr(
        db.events,
        "append_event",
        lambda event: (_ for _ in ()).throw(RuntimeError("event fault")),
    )
    with pytest.raises(RuntimeError, match="event fault"):
        uow.settle_task_attempt_with_event(
            run_id=run_id,
            task_id=task_id,
            capability="implementation",
            spec={"dependencies": []},
            result={"status": "success"},
            attempt_id="attempt-fault",
            settled_cost_usd="0.25",
            tool_receipts=[],
            event=_event(run_id=run_id, task_id=task_id, event_type="task.completed"),
        )
    assert db.get_task(run_id, task_id)["status"] == "running"
    assert (
        db.conn.execute(
            "SELECT state FROM task_attempts WHERE attempt_id='attempt-fault'"
        ).fetchone()[0]
        == "started"
    )
    assert (
        db.conn.execute(
            "SELECT state FROM budget_reservations WHERE attempt_id='attempt-fault'"
        ).fetchone()[0]
        == "reserved"
    )
    db.close()
