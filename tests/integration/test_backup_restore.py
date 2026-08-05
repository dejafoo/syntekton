"""Opt-in backup/restore integration covering completed-run recovery (PM5.E)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from product_factory.persistence.backup import create_backup, restore_backup
from product_factory.persistence.database import Database

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("BACKUP_INTEGRATION") != "1",
    reason="set BACKUP_INTEGRATION=1 for the opt-in backup/restore drill",
)
def test_backup_restore_recovers_completed_run_and_evidence(tmp_path: Path) -> None:
    source = tmp_path / "live-data"
    source.mkdir()
    db = Database(source / "data" / "product_factory.sqlite")
    run_id = "run-live-backup"
    db.upsert_run(
        run_id=run_id,
        workflow_type="release_readiness",
        status="completed",
        request={
            "request_id": "req-live",
            "workflow_type": "release_readiness",
            "request_text": "release packet",
        },
        manifest={"run_id": run_id, "final_status": "completed"},
    )
    run_dir = source / "runs" / run_id
    (run_dir / "output").mkdir(parents=True)
    (run_dir / "output" / "release_plan.json").write_text(
        json.dumps({"outcome": "ready"}),
        encoding="utf-8",
    )
    db.close()

    archive = tmp_path / "live-backup.tar.gz"
    create_backup(source, archive)
    restored = tmp_path / "restored-data"
    result = restore_backup(archive, restored)
    assert run_id in result.verified_run_ids
    db2 = Database(restored / "data" / "product_factory.sqlite")
    row = db2.get_run(run_id)
    assert row is not None
    assert row["status"] == "completed"
    assert (restored / "runs" / run_id / "output" / "release_plan.json").is_file()
    db2.close()
