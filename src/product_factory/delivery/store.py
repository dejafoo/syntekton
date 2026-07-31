"""Server-side delivery manifest, blob, and receipt store."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from product_factory.delivery.models import DeliveryEntry, DeliveryManifest, LandingReceipt
from product_factory.domain.runs import RunRequest
from product_factory.workflows.registry import land_map_for_request

_CODE_WORKFLOWS = {"code_change", "repository_change"}


class DeliveryError(RuntimeError):
    """A delivery cannot be safely built or read."""


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_manifest_bytes(manifest: DeliveryManifest) -> bytes:
    payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _safe_relative(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    candidate = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("~")
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise DeliveryError(f"Unsafe delivery destination: {path!r}")
    return str(candidate)


class DeliveryStore:
    """Filesystem-backed immutable blobs and append-only landing receipts."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.resolve()
        self.root = self.data_dir / "deliveries"

    def build(self, run_id: str, run_row: dict[str, Any]) -> DeliveryManifest:
        existing = self.get_manifest(run_id, required=False)
        if existing is not None:
            return existing

        run_dir = self.data_dir / "runs" / run_id
        request = RunRequest.model_validate(json.loads(run_row["request_json"]))
        workflow = str(run_row.get("workflow_type") or request.workflow_type)
        approval_path = run_dir / "output" / "approval.json"
        approval: dict[str, Any] = {}
        if approval_path.is_file():
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
        if workflow in _CODE_WORKFLOWS:
            if approval.get("status") != "approved":
                raise DeliveryError("Code delivery requires an approved run")
        elif str(run_row.get("status")) != "completed":
            raise DeliveryError("Document delivery requires a completed run")

        base_revision = str(run_row.get("base_commit") or "")
        if not base_revision:
            raise DeliveryError("Delivery requires a pinned base revision")

        entries: list[DeliveryEntry] = []
        output_dir = run_dir / "output"
        if workflow in _CODE_WORKFLOWS:
            patch = output_dir / "proposed.patch"
            if not patch.is_file():
                raise DeliveryError("Approved run is missing proposed.patch")
            content = patch.read_bytes()
            changed = [
                _safe_relative(str(item))
                for item in (approval.get("changed_files") or [])
                if str(item).strip()
            ]
            entries.append(
                self._put_entry(
                    run_id,
                    role="proposed_patch",
                    logical_name="proposed.patch",
                    content=content,
                    media_type="text/x-diff",
                    kind="patch",
                    changed_paths=changed,
                )
            )
        else:
            land_map = land_map_for_request(request)
            for item in land_map.landable():
                source = output_dir / item.logical_name
                if not source.is_file():
                    if item.required:
                        raise DeliveryError(
                            f"Completed run is missing required deliverable {item.logical_name}"
                        )
                    continue
                entries.append(
                    self._put_entry(
                        run_id,
                        role=item.role,
                        logical_name=item.logical_name,
                        content=source.read_bytes(),
                        media_type=item.media_type,
                        kind="file",
                        suggested_dest_path=_safe_relative(item.dest_path),
                    )
                )
        if not entries:
            raise DeliveryError("Run has no landable delivery entries")

        provenance = request.workspace_provenance
        manifest = DeliveryManifest(
            delivery_id=f"delivery-{uuid.uuid4().hex}",
            run_id=run_id,
            base_revision=base_revision,
            workspace_provenance=provenance,
            entries=entries,
        )
        manifest.manifest_sha256 = _digest(_canonical_manifest_bytes(manifest))
        target = self._run_root(run_id) / "manifest.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        self._write_exclusive(target, manifest.model_dump_json(indent=2).encode())
        return manifest

    def get_manifest(self, run_id: str, *, required: bool = True) -> DeliveryManifest | None:
        path = self._run_root(run_id) / "manifest.json"
        if not path.is_file():
            if required:
                raise DeliveryError("Delivery manifest not found")
            return None
        manifest = DeliveryManifest.model_validate_json(path.read_text(encoding="utf-8"))
        actual = _digest(_canonical_manifest_bytes(manifest))
        if actual != manifest.manifest_sha256:
            raise DeliveryError("Delivery manifest digest mismatch")
        return manifest

    def get_blob(self, run_id: str, sha256: str) -> bytes:
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise DeliveryError("Invalid blob digest")
        manifest = self.get_manifest(run_id)
        assert manifest is not None
        if not any(entry.blob_sha256 == sha256 for entry in manifest.entries):
            raise DeliveryError("Blob does not belong to this run")
        path = self._run_root(run_id) / "blobs" / sha256
        if not path.is_file():
            raise DeliveryError("Delivery blob not found")
        content = path.read_bytes()
        if _digest(content) != sha256:
            raise DeliveryError("Delivery blob digest mismatch")
        return content

    def append_receipt(self, run_id: str, receipt: LandingReceipt) -> dict[str, Any]:
        manifest = self.get_manifest(run_id)
        assert manifest is not None
        if receipt.manifest_sha256 != manifest.manifest_sha256:
            raise DeliveryError("Receipt manifest digest does not match delivery")
        if receipt.base_revision != manifest.base_revision:
            raise DeliveryError("Receipt base revision does not match delivery")
        safe_paths = [_safe_relative(path) for path in receipt.landed_paths]
        record = {
            "receipt_id": f"receipt-{uuid.uuid4().hex}",
            "run_id": run_id,
            "recorded_at": datetime.now(UTC).isoformat(),
            **receipt.model_dump(mode="json"),
            "landed_paths": safe_paths,
        }
        path = self._run_root(run_id) / "landing-receipts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, sort_keys=True) + "\n"
        fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        return record

    def _put_entry(
        self,
        run_id: str,
        *,
        role: str,
        logical_name: str,
        content: bytes,
        media_type: str,
        kind: str,
        suggested_dest_path: str | None = None,
        changed_paths: list[str] | None = None,
    ) -> DeliveryEntry:
        digest = _digest(content)
        target = self._run_root(run_id) / "blobs" / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _digest(target.read_bytes()) != digest:
                raise DeliveryError("Existing delivery blob digest mismatch")
        else:
            self._write_exclusive(target, content)
        return DeliveryEntry(
            role=role,
            logical_name=logical_name,
            blob_sha256=digest,
            size_bytes=len(content),
            media_type=media_type,
            kind=kind,  # type: ignore[arg-type]
            suggested_dest_path=suggested_dest_path,
            changed_paths=changed_paths or [],
        )

    def _run_root(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
            raise DeliveryError("Invalid run id")
        return self.root / run_id

    @staticmethod
    def _write_exclusive(path: Path, content: bytes) -> None:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
