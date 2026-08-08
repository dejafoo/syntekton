"""Dry-run-first retention and maintenance service (SD3.D)."""

from __future__ import annotations

import contextlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from product_factory.domain.errors import ConfigurationError, UnsafeOperationError
from product_factory.persistence.database import Database


@dataclass(slots=True)
class InventoryItem:
    run_id: str
    status: str
    age_days: float
    retention_class: str
    size_bytes: int
    reachable: bool
    pinned: bool


@dataclass(slots=True)
class MaintenancePlan:
    dry_run: bool
    items: list[InventoryItem] = field(default_factory=list)
    prune_run_ids: list[str] = field(default_factory=list)
    gc_blob_paths: list[str] = field(default_factory=list)
    scratch_paths: list[str] = field(default_factory=list)
    disk_warning: bool = False
    stop_admission: bool = False
    backup_ref: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "items": [
                {
                    "run_id": i.run_id,
                    "status": i.status,
                    "age_days": i.age_days,
                    "retention_class": i.retention_class,
                    "size_bytes": i.size_bytes,
                    "reachable": i.reachable,
                    "pinned": i.pinned,
                }
                for i in self.items
            ],
            "prune_run_ids": self.prune_run_ids,
            "gc_blob_paths": self.gc_blob_paths,
            "scratch_paths": self.scratch_paths,
            "disk_warning": self.disk_warning,
            "stop_admission": self.stop_admission,
            "backup_ref": self.backup_ref,
            "notes": self.notes,
        }


class MaintenanceService:
    """Inventory, pin, prune, and GC with dry-run default and append-only audit."""

    def __init__(
        self,
        *,
        data_dir: Path,
        db: Database,
        disk_warning_bytes: int = 5 * 1024**3,
        stop_admission_bytes: int = 1 * 1024**3,
    ) -> None:
        self.data_dir = data_dir.resolve()
        self.db = db
        self.disk_warning_bytes = disk_warning_bytes
        self.stop_admission_bytes = stop_admission_bytes

    def inventory(self, *, max_age_days: float | None = None) -> list[InventoryItem]:
        pinned = self._pinned_ids("run")
        items: list[InventoryItem] = []
        now = datetime.now(UTC)
        runs_dir = self.data_dir / "runs"
        for row in self.db.list_runs(limit=10_000):
            run_id = str(row["run_id"])
            created = datetime.fromisoformat(str(row["created_at"]))
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            age = (now - created).total_seconds() / 86400.0
            if max_age_days is not None and age < max_age_days:
                continue
            run_path = runs_dir / run_id
            size = _dir_size(run_path) if run_path.is_dir() else 0
            retention = "run"
            instances = self.db.list_artifact_instances(run_id)
            if instances:
                retention = str(instances[0].get("retention") or "run")
            items.append(
                InventoryItem(
                    run_id=run_id,
                    status=str(row["status"]),
                    age_days=round(age, 3),
                    retention_class=retention,
                    size_bytes=size,
                    reachable=run_path.is_dir(),
                    pinned=run_id in pinned,
                )
            )
        return items

    def plan(
        self,
        *,
        dry_run: bool = True,
        prune_run_ids: list[str] | None = None,
        max_age_days: float | None = None,
        backup_ref: str | None = None,
    ) -> MaintenancePlan:
        items = self.inventory(max_age_days=None)
        pinned = self._pinned_ids("run")
        free = _disk_free(self.data_dir)
        plan = MaintenancePlan(
            dry_run=dry_run,
            items=items,
            backup_ref=backup_ref,
            disk_warning=free < self.disk_warning_bytes,
            stop_admission=free < self.stop_admission_bytes,
        )
        if plan.disk_warning:
            plan.notes.append(f"disk free below warning threshold ({free} bytes)")
        if plan.stop_admission:
            plan.notes.append(f"disk free below stop-admission threshold ({free} bytes)")

        candidates: list[str] = []
        if prune_run_ids:
            candidates = list(prune_run_ids)
        elif max_age_days is not None:
            cutoff = datetime.now(UTC) - timedelta(days=max_age_days)
            for item in items:
                created_row = self.db.get_run(item.run_id)
                if not created_row:
                    continue
                created = datetime.fromisoformat(str(created_row["created_at"]))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=UTC)
                if created <= cutoff and item.run_id not in pinned:
                    candidates.append(item.run_id)

        for run_id in candidates:
            if run_id in pinned:
                plan.notes.append(f"skip pinned run {run_id}")
                continue
            # Never accept unresolved filesystem paths as deletion targets.
            if "/" in run_id or "\\" in run_id or ".." in run_id or run_id in {".", ""}:
                raise UnsafeOperationError(
                    "Deletion targets must be explicit run IDs, not paths",
                    details={"run_id": run_id},
                )
            plan.prune_run_ids.append(run_id)

        plan.gc_blob_paths = self._unreachable_blob_rels(exclude_runs=set(plan.prune_run_ids))
        plan.scratch_paths = self._stale_scratch_rels()
        return plan

    def execute(
        self,
        plan: MaintenancePlan,
        *,
        actor: str = "operator",
        require_backup: bool = True,
    ) -> MaintenancePlan:
        if plan.dry_run:
            self._audit(
                action="maintenance_plan",
                dry_run=True,
                actor=actor,
                payload=plan.to_dict(),
                backup_ref=plan.backup_ref,
            )
            return plan

        if require_backup and plan.prune_run_ids:
            if not plan.backup_ref:
                raise UnsafeOperationError(
                    "Material pruning requires an eligible backup_ref",
                    details={"prune_run_ids": plan.prune_run_ids},
                )
            backup_path = Path(plan.backup_ref)
            if not backup_path.is_file():
                raise UnsafeOperationError(
                    "Backup prerequisite missing or not a file",
                    details={"backup_ref": plan.backup_ref},
                )

        for run_id in plan.prune_run_ids:
            self._delete_run_tree(run_id)

        for rel in plan.gc_blob_paths + plan.scratch_paths:
            self._delete_rel(rel)

        # WAL checkpoint; VACUUM only when explicitly noted on the plan.
        self.db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if any(n.startswith("vacuum:") for n in plan.notes):
            self.db.conn.execute("VACUUM")

        self._audit(
            action="maintenance_execute",
            dry_run=False,
            actor=actor,
            payload=plan.to_dict(),
            backup_ref=plan.backup_ref,
        )
        return plan

    def pin(
        self, *, target_kind: str, target_id: str, reason: str = "", actor: str = "operator"
    ) -> None:
        if target_kind not in {"run", "experiment"}:
            raise ConfigurationError(f"Unsupported pin kind: {target_kind}")
        if "/" in target_id or ".." in target_id:
            raise UnsafeOperationError("Pin targets must be IDs, not paths")
        now = datetime.now(UTC).isoformat()
        pin_id = f"{target_kind}:{target_id}"

        def _write(conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO retention_pins(pin_id, target_kind, target_id, reason, pinned_at, pinned_by)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(target_kind, target_id) DO UPDATE SET
                  reason=excluded.reason, pinned_at=excluded.pinned_at, pinned_by=excluded.pinned_by
                """,
                (pin_id, target_kind, target_id, reason, now, actor),
            )
            conn.commit()

        self.db._actor.run(_write)
        self._audit(
            action="pin",
            dry_run=False,
            actor=actor,
            payload={"target_kind": target_kind, "target_id": target_id, "reason": reason},
        )

    def unpin(self, *, target_kind: str, target_id: str, actor: str = "operator") -> None:
        def _write(conn: Any) -> None:
            conn.execute(
                "DELETE FROM retention_pins WHERE target_kind=? AND target_id=?",
                (target_kind, target_id),
            )
            conn.commit()

        self.db._actor.run(_write)
        self._audit(
            action="unpin",
            dry_run=False,
            actor=actor,
            payload={"target_kind": target_kind, "target_id": target_id},
        )

    def list_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        def _read(conn: Any) -> list[dict[str, Any]]:
            rows = conn.execute(
                """
                SELECT audit_id, recorded_at, action, dry_run, actor, payload_json, backup_ref
                FROM maintenance_audit
                ORDER BY audit_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["payload"] = json.loads(item.pop("payload_json"))
                item["dry_run"] = bool(item["dry_run"])
                out.append(item)
            return out

        return self.db._actor.run(_read)

    def _pinned_ids(self, kind: str) -> set[str]:
        def _read(conn: Any) -> set[str]:
            rows = conn.execute(
                "SELECT target_id FROM retention_pins WHERE target_kind=?",
                (kind,),
            ).fetchall()
            return {str(r["target_id"]) for r in rows}

        return self.db._actor.run(_read)

    def _audit(
        self,
        *,
        action: str,
        dry_run: bool,
        actor: str,
        payload: dict[str, Any],
        backup_ref: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()

        def _write(conn: Any) -> None:
            conn.execute(
                """
                INSERT INTO maintenance_audit(recorded_at, action, dry_run, actor, payload_json, backup_ref)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    action,
                    1 if dry_run else 0,
                    actor,
                    json.dumps(payload, default=str, sort_keys=True),
                    backup_ref,
                ),
            )
            conn.commit()

        self.db._actor.run(_write)

    def _delete_run_tree(self, run_id: str) -> None:
        run_dir = (self.data_dir / "runs" / run_id).resolve()
        runs_root = (self.data_dir / "runs").resolve()
        if not str(run_dir).startswith(str(runs_root) + "/") and run_dir != runs_root:
            raise UnsafeOperationError(
                "Refusing to delete path outside runs root",
                details={"path": str(run_dir)},
            )
        if run_dir.is_dir():
            shutil.rmtree(run_dir)

    def _delete_rel(self, rel: str) -> None:
        if rel.startswith("/") or ".." in Path(rel).parts:
            raise UnsafeOperationError("Refusing unresolved deletion path", details={"rel": rel})
        path = (self.data_dir / rel).resolve()
        if not str(path).startswith(str(self.data_dir)):
            raise UnsafeOperationError(
                "Refusing to delete outside data root",
                details={"path": str(path)},
            )
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _unreachable_blob_rels(self, *, exclude_runs: set[str]) -> list[str]:
        referenced: set[str] = set()
        for row in self.db.list_runs(limit=10_000):
            run_id = str(row["run_id"])
            if run_id in exclude_runs:
                continue
            for inst in self.db.list_artifact_instances(run_id):
                referenced.add(str(inst["sha256"]))
        found: list[str] = []
        runs_dir = self.data_dir / "runs"
        if not runs_dir.is_dir():
            return found
        for blob in runs_dir.rglob("blobs/*"):
            if not blob.is_file():
                continue
            if blob.name not in referenced:
                rel = str(blob.relative_to(self.data_dir)).replace("\\", "/")
                found.append(rel)
        return found

    def _stale_scratch_rels(self) -> list[str]:
        out: list[str] = []
        for name in ("uploads", "scratch"):
            root = self.data_dir / name
            if not root.is_dir():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    # Only list; age policy can be tightened later.
                    out.append(str(path.relative_to(self.data_dir)).replace("\\", "/"))
        return out


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            with contextlib.suppress(OSError):
                total += p.stat().st_size
    return total


def _disk_free(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return int(usage.free)
