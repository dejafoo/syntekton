"""Data-root backup and restore helpers (PM5.E)."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError, UnsafeOperationError

MANIFEST_NAME = "backup-manifest.json"


class BackupManifest(BaseModel):
    created_at: str
    source_data_dir: str
    includes: list[str] = Field(default_factory=list)
    sqlite_sha256: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    file_count: int = 0
    byte_count: int = 0


class RestoreResult(BaseModel):
    restored_at: str
    target_data_dir: str
    manifest: BackupManifest
    verified_run_ids: list[str] = Field(default_factory=list)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _assert_safe_member(member: tarfile.TarInfo, dest: Path) -> None:
    name = member.name
    if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
        raise UnsafeOperationError(
            "Backup archive member escapes destination",
            details={"member": name},
        )
    target = (dest / name).resolve()
    if not str(target).startswith(str(dest.resolve())):
        raise UnsafeOperationError(
            "Backup archive member escapes destination",
            details={"member": name},
        )


def create_backup(data_dir: Path, dest: Path) -> BackupManifest:
    """Snapshot SQLite + runs/ (+ ops/) into a tar.gz with an integrity manifest."""
    root = data_dir.resolve()
    if not root.is_dir():
        raise ConfigurationError(f"Data directory missing: {root}")
    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    db_path = root / "data" / "product_factory.sqlite"
    run_ids: list[str] = []
    runs_dir = root / "runs"
    if runs_dir.is_dir():
        run_ids = sorted(p.name for p in runs_dir.iterdir() if p.is_dir())

    sqlite_sha: str | None = None
    with tempfile.TemporaryDirectory(prefix="pf-backup-") as tmp:
        staging = Path(tmp)
        included: list[str] = []
        byte_count = 0
        file_count = 0

        if db_path.is_file():
            data_staging = staging / "data"
            data_staging.mkdir(parents=True)
            backup_db = data_staging / "product_factory.sqlite"
            # Prefer SQLite online backup API for a consistent snapshot.
            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                dst = sqlite3.connect(backup_db)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            finally:
                src.close()
            sqlite_sha = _sha256_file(backup_db)
            included.append("data/product_factory.sqlite")
            file_count += 1
            byte_count += backup_db.stat().st_size

        for rel in ("runs", "ops", "uploads"):
            src_dir = root / rel
            if not src_dir.exists():
                continue
            dst_dir = staging / rel
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
            included.append(f"{rel}/")
            for path in dst_dir.rglob("*"):
                if path.is_file():
                    file_count += 1
                    byte_count += path.stat().st_size

        manifest = BackupManifest(
            created_at=datetime.now(UTC).isoformat(),
            source_data_dir=str(root),
            includes=included,
            sqlite_sha256=sqlite_sha,
            run_ids=run_ids,
            file_count=file_count,
            byte_count=byte_count,
        )
        (staging / MANIFEST_NAME).write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        with tarfile.open(dest, "w:gz") as archive:
            archive.add(staging, arcname=".")
    return manifest


def restore_backup(archive: Path, data_dir: Path, *, replace: bool = False) -> RestoreResult:
    """Restore a backup archive into ``data_dir``.

    When ``replace`` is true, an existing data root is moved aside first.
    """
    archive = archive.resolve()
    if not archive.is_file():
        raise ConfigurationError(f"Backup archive missing: {archive}")
    target = data_dir.resolve()
    if target.exists() and any(target.iterdir()):
        if not replace:
            raise UnsafeOperationError(
                "Target data directory is not empty; pass replace=True",
                details={"target": str(target)},
            )
        aside = target.with_name(
            f"{target.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )
        target.rename(aside)

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pf-restore-") as tmp:
        extract_root = Path(tmp) / "extract"
        extract_root.mkdir()
        with tarfile.open(archive, "r:gz") as tf:
            for member in tf.getmembers():
                _assert_safe_member(member, extract_root)
            # Python 3.12+ supports filter=; prefer data extraction when available.
            try:
                tf.extractall(extract_root, filter="data")
            except TypeError:
                tf.extractall(extract_root)

        # Archives are written with arcname=".", so content may sit at extract_root
        # or one nested directory depending on tar tooling.
        candidates = [extract_root, *extract_root.iterdir()]
        payload = next(
            (c for c in candidates if c.is_dir() and (c / MANIFEST_NAME).is_file()),
            None,
        )
        if payload is None:
            raise ConfigurationError("Backup archive missing backup-manifest.json")
        manifest = BackupManifest.model_validate_json(
            (payload / MANIFEST_NAME).read_text(encoding="utf-8")
        )
        for child in payload.iterdir():
            if child.name == MANIFEST_NAME:
                continue
            dest = target / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
            else:
                shutil.copy2(child, dest)

        db_path = target / "data" / "product_factory.sqlite"
        if manifest.sqlite_sha256 and db_path.is_file():
            actual = _sha256_file(db_path)
            if actual != manifest.sqlite_sha256:
                raise UnsafeOperationError(
                    "Restored SQLite digest mismatch",
                    details={
                        "expected": manifest.sqlite_sha256,
                        "actual": actual,
                    },
                )

        verified = [run_id for run_id in manifest.run_ids if (target / "runs" / run_id).is_dir()]
        return RestoreResult(
            restored_at=datetime.now(UTC).isoformat(),
            target_data_dir=str(target),
            manifest=manifest,
            verified_run_ids=verified,
        )


def backup_status(data_dir: Path) -> dict[str, Any]:
    root = data_dir.resolve()
    db = root / "data" / "product_factory.sqlite"
    runs = root / "runs"
    return {
        "data_dir": str(root),
        "sqlite_present": db.is_file(),
        "sqlite_bytes": db.stat().st_size if db.is_file() else 0,
        "run_count": len([p for p in runs.iterdir() if p.is_dir()]) if runs.is_dir() else 0,
    }
