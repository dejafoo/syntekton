"""Data-root backup and restore helpers with per-file manifests (SD3.C)."""

from __future__ import annotations

import hashlib
import json
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

# Paths under the data root that backups include when present.
BACKUP_TREE_ROOTS: tuple[str, ...] = ("runs", "ops", "uploads", "content", "experiments")

# Configuration/skills/profiles live outside the data root; operators must
# back those up separately (documented in evidence and ops help text).
EXTERNAL_BACKUP_NOTE = (
    "Configuration, skills, and model profiles live outside the data root and "
    "are not included in this archive; back them up separately."
)


class FileChecksum(BaseModel):
    relative_path: str
    sha256: str
    size_bytes: int


class BackupManifest(BaseModel):
    created_at: str
    source_data_dir: str
    includes: list[str] = Field(default_factory=list)
    sqlite_sha256: str | None = None
    run_ids: list[str] = Field(default_factory=list)
    file_count: int = 0
    byte_count: int = 0
    # SD3.C enrichments
    high_water_event_seq: int | None = None
    file_checksums: list[FileChecksum] = Field(default_factory=list)
    external_backup_note: str = EXTERNAL_BACKUP_NOTE
    manifest_version: int = 2


class RestoreValidation(BaseModel):
    missing_blobs: list[str] = Field(default_factory=list)
    orphan_blobs: list[str] = Field(default_factory=list)
    corrupt_blobs: list[str] = Field(default_factory=list)
    missing_runs: list[str] = Field(default_factory=list)
    checksum_mismatches: list[str] = Field(default_factory=list)
    high_water_event_seq: int | None = None
    ok: bool = True


class RestoreResult(BaseModel):
    restored_at: str
    target_data_dir: str
    manifest: BackupManifest
    verified_run_ids: list[str] = Field(default_factory=list)
    validation: RestoreValidation | None = None


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


def _high_water_event_seq(db_path: Path) -> int | None:
    if not db_path.is_file():
        return None
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()
        return int(row[0] if row else 0)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def create_backup(data_dir: Path, dest: Path) -> BackupManifest:
    """Snapshot SQLite + durable trees into a tar.gz with per-file checksums."""
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

    high_water = _high_water_event_seq(db_path)
    sqlite_sha: str | None = None
    with tempfile.TemporaryDirectory(prefix="pf-backup-") as tmp:
        staging = Path(tmp)
        included: list[str] = []
        byte_count = 0
        file_count = 0
        checksums: list[FileChecksum] = []

        if db_path.is_file():
            data_staging = staging / "data"
            data_staging.mkdir(parents=True)
            backup_db = data_staging / "product_factory.sqlite"
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
            rel = "data/product_factory.sqlite"
            included.append(rel)
            file_count += 1
            size = backup_db.stat().st_size
            byte_count += size
            checksums.append(FileChecksum(relative_path=rel, sha256=sqlite_sha, size_bytes=size))

        for rel_root in BACKUP_TREE_ROOTS:
            src_dir = root / rel_root
            if not src_dir.exists():
                continue
            dst_dir = staging / rel_root
            shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
            included.append(f"{rel_root}/")
            for path in sorted(dst_dir.rglob("*")):
                if not path.is_file():
                    continue
                rel = str(path.relative_to(staging)).replace("\\", "/")
                digest = _sha256_file(path)
                size = path.stat().st_size
                file_count += 1
                byte_count += size
                checksums.append(FileChecksum(relative_path=rel, sha256=digest, size_bytes=size))

        manifest = BackupManifest(
            created_at=datetime.now(UTC).isoformat(),
            source_data_dir=str(root),
            includes=included,
            sqlite_sha256=sqlite_sha,
            run_ids=run_ids,
            file_count=file_count,
            byte_count=byte_count,
            high_water_event_seq=high_water,
            file_checksums=checksums,
        )
        (staging / MANIFEST_NAME).write_text(
            manifest.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
        with tarfile.open(dest, "w:gz") as archive:
            archive.add(staging, arcname=".")
    return manifest


def validate_restored_root(target: Path, manifest: BackupManifest) -> RestoreValidation:
    """Validate restored files against manifest checksums and DB references."""
    result = RestoreValidation(high_water_event_seq=manifest.high_water_event_seq)
    for entry in manifest.file_checksums:
        path = target / entry.relative_path
        if not path.is_file():
            result.checksum_mismatches.append(entry.relative_path)
            continue
        actual = _sha256_file(path)
        if actual != entry.sha256 or path.stat().st_size != entry.size_bytes:
            result.checksum_mismatches.append(entry.relative_path)

    for run_id in manifest.run_ids:
        if not (target / "runs" / run_id).is_dir():
            result.missing_runs.append(run_id)

    db_path = target / "data" / "product_factory.sqlite"
    if db_path.is_file():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT DISTINCT sha256 FROM artifact_instances").fetchall()
            referenced = {str(r["sha256"]) for r in rows}
        except sqlite3.Error:
            referenced = set()
        finally:
            conn.close()
        blob_root = target / "runs"
        present: set[str] = set()
        if blob_root.is_dir():
            for blob in blob_root.rglob("blobs/*"):
                if blob.is_file() and "/" not in blob.name and "\\" not in blob.name:
                    present.add(blob.name)
                    if referenced and blob.name in referenced:
                        try:
                            actual = _sha256_file(blob)
                            if actual != blob.name:
                                result.corrupt_blobs.append(blob.name)
                        except OSError:
                            result.corrupt_blobs.append(blob.name)
        if referenced:
            result.missing_blobs = sorted(referenced - present)
            # Orphans are present blobs never referenced; only report under runs/*/artifacts/blobs
            result.orphan_blobs = sorted(present - referenced)[:50]

    result.ok = not (
        result.missing_blobs
        or result.corrupt_blobs
        or result.missing_runs
        or result.checksum_mismatches
    )
    return result


def restore_backup(
    archive: Path,
    data_dir: Path,
    *,
    replace: bool = False,
    validate: bool = True,
) -> RestoreResult:
    """Restore a backup archive into ``data_dir`` and optionally validate."""
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
            try:
                tf.extractall(extract_root, filter="data")
            except TypeError:
                tf.extractall(extract_root)

        candidates = [extract_root, *extract_root.iterdir()]
        payload = next(
            (c for c in candidates if c.is_dir() and (c / MANIFEST_NAME).is_file()),
            None,
        )
        if payload is None:
            raise ConfigurationError("Backup archive missing backup-manifest.json")
        raw = json.loads((payload / MANIFEST_NAME).read_text(encoding="utf-8"))
        # Accept v1 manifests missing SD3 fields.
        manifest = BackupManifest.model_validate(raw)
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
        validation = validate_restored_root(target, manifest) if validate else None
        return RestoreResult(
            restored_at=datetime.now(UTC).isoformat(),
            target_data_dir=str(target),
            manifest=manifest,
            verified_run_ids=verified,
            validation=validation,
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
        "high_water_event_seq": _high_water_event_seq(db),
        "external_backup_note": EXTERNAL_BACKUP_NOTE,
    }
