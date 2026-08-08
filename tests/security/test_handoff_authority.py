"""SD0.B durable handoff authority tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from product_factory.persistence.database import Database
from product_factory.trust.handoffs import HandoffRefusal, HandoffService


def _pack() -> SimpleNamespace:
    return SimpleNamespace(
        id="test",
        execution_policy=SimpleNamespace(
            accepted_handoff_schemas=frozenset({"release_plan.v1"}),
            accepted_handoff_states=frozenset({"approved"}),
            accepted_handoff_roles={"release_plan.v1": frozenset({"release_plan"})},
        ),
    )


def _seed(service: HandoffService, root: Path) -> tuple[str, dict[str, str]]:
    service.db.upsert_run(run_id="producer", workflow_type="test", status="completed", request={})
    service.db.upsert_run(run_id="consumer", workflow_type="test", status="queued", request={})
    content = b"release plan"
    digest = hashlib.sha256(content).hexdigest()
    instance_id = "producer-artifact"
    service.db.record_artifact_instance(
        {
            "instance_id": instance_id,
            "run_id": "producer",
            "sha256": digest,
            "role": "release_plan",
            "producer_task_id": "plan",
            "media_type": "application/json",
            "schema_id": "release_plan.v1",
            "size_bytes": len(content),
        }
    )
    blob = root / "runs" / "producer" / "artifacts" / "blobs" / digest
    blob.parent.mkdir(parents=True)
    blob.write_bytes(content)
    handoff = service.create_from_artifact_instance(instance_id, role="release_plan")
    service.promote_evidence_complete(handoff.handoff_id)
    service.approve(handoff.handoff_id, actor="operator")
    return handoff.handoff_id, {
        "schema_id": "release_plan.v1",
        "digest": digest,
        "producer_run_id": "producer",
        "producer_task_id": "plan",
        "role": "release_plan",
        "state": "approved",
    }


def test_forged_handoff_assertions_refuse_before_consumer_lineage(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    service = HandoffService(db, tmp_path)
    _, ref = _seed(service, tmp_path)
    ref["digest"] = "0" * 64

    with pytest.raises(HandoffRefusal):
        service.resolve_refs({"handoff_refs": [ref]}, _pack(), consumer_run_id="consumer")

    assert db.list_artifact_instances("consumer") == []


def test_resolution_reresolves_on_resume_without_duplicate_consumption(tmp_path: Path) -> None:
    db = Database(tmp_path / "db.sqlite")
    service = HandoffService(db, tmp_path)
    handoff_id, ref = _seed(service, tmp_path)

    first = service.resolve_refs({"handoff_refs": [ref]}, _pack(), consumer_run_id="consumer")
    resumed = service.resolve_refs({"handoff_refs": [ref]}, _pack(), consumer_run_id="consumer")

    assert first[0].record.handoff_id == handoff_id
    assert resumed[0].consumption.consumer_artifact_instance_id == (
        first[0].consumption.consumer_artifact_instance_id
    )
