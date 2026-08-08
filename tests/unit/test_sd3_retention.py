"""SD3.D retention / maintenance dry-run and audit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from product_factory.domain.errors import UnsafeOperationError
from product_factory.persistence.backup import create_backup
from product_factory.persistence.database import Database
from product_factory.persistence.retention import MaintenanceService


def _seed(root: Path, run_id: str = "run-keep") -> Database:
    db = Database(root / "data" / "product_factory.sqlite")
    db.upsert_run(
        run_id=run_id,
        workflow_type="code_change",
        status="completed",
        request={"request_id": "r"},
    )
    (root / "runs" / run_id).mkdir(parents=True)
    (root / "runs" / run_id / "note.txt").write_text("x", encoding="utf-8")
    return db


def test_dry_run_does_not_delete(tmp_path: Path) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    db = _seed(root, "run-1")
    svc = MaintenanceService(data_dir=root, db=db)
    plan = svc.plan(dry_run=True, prune_run_ids=["run-1"])
    assert plan.prune_run_ids == ["run-1"]
    svc.execute(plan)
    assert (root / "runs" / "run-1").is_dir()
    audit = svc.list_audit()
    assert audit and audit[0]["dry_run"] is True
    db.close()


def test_execute_requires_backup_and_respects_pin(tmp_path: Path) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    db = _seed(root, "run-1")
    svc = MaintenanceService(data_dir=root, db=db)
    svc.pin(target_kind="run", target_id="run-1", reason="keep")
    plan = svc.plan(dry_run=False, prune_run_ids=["run-1"])
    assert plan.prune_run_ids == []
    assert any("pinned" in n for n in plan.notes)

    db.upsert_run(
        run_id="run-2",
        workflow_type="code_change",
        status="completed",
        request={"request_id": "r2"},
    )
    (root / "runs" / "run-2").mkdir(parents=True)
    plan2 = svc.plan(dry_run=False, prune_run_ids=["run-2"])
    with pytest.raises(UnsafeOperationError, match="backup"):
        svc.execute(plan2, require_backup=True)

    archive = tmp_path / "elig.tar.gz"
    create_backup(root, archive)
    plan2.backup_ref = str(archive)
    svc.execute(plan2, require_backup=True)
    assert not (root / "runs" / "run-2").exists()
    assert (root / "runs" / "run-1").exists()
    audit = svc.list_audit()
    assert any(a["action"] == "maintenance_execute" and not a["dry_run"] for a in audit)
    db.close()


def test_refuses_path_shaped_prune_targets(tmp_path: Path) -> None:
    root = tmp_path / "data-root"
    root.mkdir()
    db = _seed(root)
    svc = MaintenanceService(data_dir=root, db=db)
    with pytest.raises(UnsafeOperationError, match="explicit run IDs"):
        svc.plan(dry_run=True, prune_run_ids=["../etc/passwd"])
    db.close()
