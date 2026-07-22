"""Integration: concurrent SQLite writer + reader / mid-run attach semantics."""

from __future__ import annotations

import threading
from pathlib import Path

from product_factory.observability.contracts import CaptureLevel
from product_factory.observability.query import ObservabilityQueryService
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.persistence.database import Database


def test_concurrent_writer_and_reader(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "product_factory.sqlite"
    db_path.parent.mkdir(parents=True)
    writer = Database(db_path)
    writer.upsert_run(
        run_id="run-c",
        workflow_type="code_change",
        status="running",
        request={},
    )
    recorder = TelemetryRecorder(writer, capture_level=CaptureLevel.METADATA)
    errors: list[BaseException] = []

    def produce() -> None:
        try:
            for i in range(40):
                recorder.emit(
                    run_id="run-c",
                    event_type="heartbeat",
                    summary=str(i),
                    payload={"i": i},
                )
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=produce)
    t.start()
    reader = Database(db_path)
    query = ObservabilityQueryService(reader)
    seen = 0
    cursor = 0
    while seen < 40 and t.is_alive() or cursor < 40:
        batch = query.list_events(run_id="run-c", after_seq=cursor, limit=10)
        if not batch:
            if not t.is_alive() and cursor >= writer.latest_seq():
                break
            continue
        cursor = batch[-1]["seq"]
        seen = cursor
    t.join(timeout=5)
    assert not errors
    assert writer.latest_seq() == 40
    assert query.list_events(run_id="run-c", after_seq=0, limit=100)
    reader.close()
    writer.close()


def test_tool_broker_emits_started_before_complete(tmp_path: Path) -> None:
    from product_factory.domain.tools import CapabilityGrant
    from product_factory.persistence.artifacts import ArtifactStore
    from product_factory.tools.broker import ToolBroker
    from product_factory.tools.registry import default_tool_registry

    phases: list[str] = []

    def obs(phase: str, payload: dict) -> None:
        phases.append(phase)

    wt = tmp_path / "wt"
    wt.mkdir()
    store = ArtifactStore(tmp_path / "artifacts")
    broker = ToolBroker(
        registry=default_tool_registry(),
        artifact_store=store,
        worktree_root=wt,
        observer=obs,
    )
    broker.set_grant(
        CapabilityGrant(
            grant_id="g1",
            run_id="r1",
            task_id="t1",
            agent_profile="implementation_worker",
            tool_names={"write_artifact"},
            allowed_path_patterns=["**/*"],
            max_calls=5,
        )
    )
    broker.execute(
        task_id="t1",
        tool_name="write_artifact",
        arguments={
            "logical_name": "x.txt",
            "content": "hello",
            "media_type": "text/plain",
        },
    )
    assert phases[0] == "started"
    assert phases[-1] == "completed"
