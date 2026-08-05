"""Backup/restore unit coverage (PM5.E)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from product_factory.persistence.backup import create_backup, restore_backup
from product_factory.persistence.database import Database


def _seed_data_dir(root: Path) -> str:
    db = Database(root / "data" / "product_factory.sqlite")
    run_id = "run-backup-1"
    db.upsert_run(
        run_id=run_id,
        workflow_type="change_intake",
        status="completed",
        request={
            "request_id": "req-1",
            "workflow_type": "change_intake",
            "request_text": "backup me",
        },
        manifest={"run_id": run_id, "final_status": "completed"},
    )
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run-manifest.json").write_text(
        json.dumps({"run_id": run_id, "final_status": "completed"}),
        encoding="utf-8",
    )
    (root / "ops").mkdir(parents=True, exist_ok=True)
    (root / "ops" / "note.txt").write_text("ops", encoding="utf-8")
    db.close()
    return run_id


def test_backup_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_id = _seed_data_dir(source)
    archive = tmp_path / "pf-backup.tar.gz"
    manifest = create_backup(source, archive)
    assert run_id in manifest.run_ids
    assert manifest.sqlite_sha256
    assert archive.is_file()

    target = tmp_path / "restored"
    result = restore_backup(archive, target)
    assert run_id in result.verified_run_ids
    assert (target / "runs" / run_id / "run-manifest.json").is_file()
    assert (target / "ops" / "note.txt").read_text(encoding="utf-8") == "ops"
    db = Database(target / "data" / "product_factory.sqlite")
    row = db.get_run(run_id)
    assert row is not None
    assert row["status"] == "completed"
    db.close()


def test_restore_refuses_non_empty_target_without_replace(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _seed_data_dir(source)
    archive = tmp_path / "pf-backup.tar.gz"
    create_backup(source, archive)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep").write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="not empty"):
        restore_backup(archive, target, replace=False)
