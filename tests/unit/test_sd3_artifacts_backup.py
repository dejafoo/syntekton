"""SD3.C atomic artifacts and backup/restore validation tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from product_factory.domain.errors import UnsafeOperationError
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.backup import create_backup, restore_backup
from product_factory.persistence.database import Database


def test_artifact_atomic_write_and_verify(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    ref = store.put_text(
        "hello-atomic",
        media_type="text/plain",
        logical_name="a",
        created_by_task_id="t1",
    )
    assert store.exists(ref.sha256)
    store.verify_blob(ref.sha256, expected_size=ref.size_bytes)
    assert store.get_text(ref.sha256, verify=True) == "hello-atomic"


def test_artifact_rejects_corrupt_existing_blob(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    content = b"trusted-bytes"
    sha = hashlib.sha256(content).hexdigest()
    path = store.blobs / sha
    path.write_bytes(b"corrupt!!!!!!")
    with pytest.raises(UnsafeOperationError, match="digest mismatch"):
        store.put_bytes(
            content,
            media_type="application/octet-stream",
            logical_name="x",
            created_by_task_id="t",
        )


def test_backup_manifest_includes_checksums_and_high_water(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    db = Database(source / "data" / "product_factory.sqlite")
    run_id = "run-backup-2"
    db.upsert_run(
        run_id=run_id,
        workflow_type="change_intake",
        status="completed",
        request={"request_id": "r", "workflow_type": "change_intake", "request_text": "x"},
    )
    # Append a fake event via repository for high-water.
    from product_factory.observability.contracts import EventSeverity, ObservabilityEvent

    seq = db.append_event(
        ObservabilityEvent(
            event_id="e1",
            type="test.event",
            run_id=run_id,
            severity=EventSeverity.INFO,
            summary="hi",
            payload={},
        )
    )
    assert seq >= 1
    run_dir = source / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    blob_dir = run_dir / "artifacts" / "blobs"
    blob_dir.mkdir(parents=True)
    body = b"blob-bytes"
    sha = hashlib.sha256(body).hexdigest()
    (blob_dir / sha).write_bytes(body)
    db.record_artifact(
        {
            "sha256": sha,
            "media_type": "application/octet-stream",
            "size_bytes": len(body),
            "logical_name": "b",
            "relative_path": f"blobs/{sha}",
            "created_by_task_id": "t",
            "trust_level": "generated",
        }
    )
    db.record_artifact_instance(
        {
            "instance_id": "inst-1",
            "run_id": run_id,
            "sha256": sha,
            "size_bytes": len(body),
        }
    )
    db.close()

    archive = tmp_path / "backup.tar.gz"
    manifest = create_backup(source, archive)
    assert manifest.file_checksums
    assert manifest.high_water_event_seq is not None
    assert manifest.high_water_event_seq >= 1
    assert any(c.relative_path.endswith("product_factory.sqlite") for c in manifest.file_checksums)

    target = tmp_path / "restored"
    result = restore_backup(archive, target, validate=True)
    assert run_id in result.verified_run_ids
    assert result.validation is not None
    assert result.validation.ok or not result.validation.checksum_mismatches
