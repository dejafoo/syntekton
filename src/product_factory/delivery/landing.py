"""Fail-closed local landing for remote delivery manifests."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from product_factory.delivery.models import DeliveryManifest
from product_factory.delivery.store import _canonical_manifest_bytes


class LandingError(RuntimeError):
    """Delivery verification failed before landing completed."""


@dataclass(frozen=True)
class LandingResult:
    run_id: str
    manifest_sha256: str
    base_revision: str
    landed_paths: tuple[str, ...]


class LandingAdapter:
    """Verify every byte and repository invariant before writing locally."""

    def land(
        self,
        manifest: DeliveryManifest | dict,
        *,
        workspace_root: Path,
        blob_loader: Callable[[str], bytes],
        overwrite: bool = False,
    ) -> LandingResult:
        delivery = (
            manifest
            if isinstance(manifest, DeliveryManifest)
            else DeliveryManifest.model_validate(manifest)
        )
        expected_manifest = hashlib.sha256(_canonical_manifest_bytes(delivery)).hexdigest()
        if expected_manifest != delivery.manifest_sha256:
            raise LandingError("Delivery manifest digest mismatch")

        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise LandingError("Workspace root is not a directory")
        current = self._git_head(root)
        if current != delivery.base_revision:
            raise LandingError(
                f"Workspace base changed: expected {delivery.base_revision}, found {current}"
            )

        blobs: dict[str, bytes] = {}
        destinations: dict[str, Path] = {}
        kinds = {entry.kind for entry in delivery.entries}
        if not delivery.entries or kinds not in ({"file"}, {"patch"}):
            raise LandingError("Delivery must contain only files or one patch")
        if kinds == {"patch"} and len(delivery.entries) != 1:
            raise LandingError("Patch delivery must contain exactly one entry")

        for entry in delivery.entries:
            try:
                content = blob_loader(entry.blob_sha256)
            except Exception as exc:
                raise LandingError(f"Missing delivery blob {entry.blob_sha256}") from exc
            if hashlib.sha256(content).hexdigest() != entry.blob_sha256:
                raise LandingError(f"Delivery blob digest mismatch for {entry.logical_name}")
            if len(content) != entry.size_bytes:
                raise LandingError(f"Delivery blob size mismatch for {entry.logical_name}")
            blobs[entry.blob_sha256] = content
            if entry.kind == "file":
                if not entry.suggested_dest_path:
                    raise LandingError(f"Missing destination for {entry.logical_name}")
                destination = self._destination(root, entry.suggested_dest_path)
                if destination.exists() and not overwrite:
                    raise LandingError(f"Destination already exists: {entry.suggested_dest_path}")
                destinations[entry.suggested_dest_path] = destination

        if kinds == {"patch"}:
            entry = delivery.entries[0]
            self._apply_patch(root, blobs[entry.blob_sha256])
            landed = tuple(entry.changed_paths)
        else:
            landed = self._write_files(root, destinations, delivery, blobs)
        return LandingResult(
            run_id=delivery.run_id,
            manifest_sha256=delivery.manifest_sha256,
            base_revision=delivery.base_revision,
            landed_paths=landed,
        )

    @staticmethod
    def _git_head(root: Path) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise LandingError("Workspace root is not a Git repository")
        return result.stdout.strip()

    @staticmethod
    def _destination(root: Path, relative: str) -> Path:
        raw = Path(relative)
        if raw.is_absolute() or relative.startswith("~") or ".." in raw.parts:
            raise LandingError(f"Destination escapes workspace root: {relative}")
        target = root.joinpath(raw)
        parent = target.parent.resolve(strict=False)
        if not parent.is_relative_to(root):
            raise LandingError(f"Destination escapes workspace root: {relative}")
        if target.is_symlink() and not target.resolve(strict=False).is_relative_to(root):
            raise LandingError(f"Destination symlink escapes workspace root: {relative}")
        return target

    @staticmethod
    def _apply_patch(root: Path, patch: bytes) -> None:
        for args in (["git", "apply", "--check", "-"], ["git", "apply", "--index", "-"]):
            result = subprocess.run(
                args,
                cwd=root,
                input=patch,
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                message = result.stderr.decode(errors="replace").strip()
                raise LandingError(f"Patch landing failed: {message}")

    @staticmethod
    def _write_files(
        root: Path,
        destinations: dict[str, Path],
        delivery: DeliveryManifest,
        blobs: dict[str, bytes],
    ) -> tuple[str, ...]:
        staged: list[tuple[Path, Path]] = []
        try:
            for entry in delivery.entries:
                assert entry.suggested_dest_path is not None
                destination = destinations[entry.suggested_dest_path]
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(
                    prefix=".pf-land-",
                    dir=destination.parent,
                )
                tmp = Path(tmp_name)
                try:
                    os.write(fd, blobs[entry.blob_sha256])
                    os.fsync(fd)
                finally:
                    os.close(fd)
                staged.append((tmp, destination))
            for tmp, destination in staged:
                os.replace(tmp, destination)
            return tuple(entry.suggested_dest_path or "" for entry in delivery.entries)
        finally:
            for tmp, _ in staged:
                tmp.unlink(missing_ok=True)
