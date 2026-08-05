"""Bounded git-bundle upload preflight / accept / finalize (PM5.E)."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from product_factory.api.ingress import IngressConfig
from product_factory.domain.errors import UnsafeOperationError

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
# Git bundle v2/v3 magic ("# v2 git bundle\n" / "# v3 git bundle\n").
_BUNDLE_MAGIC = b"# v"


class UploadPreflight(BaseModel):
    model_config = {"extra": "forbid"}

    declared_size: int = Field(ge=1)
    declared_sha256: str
    media_type: str
    filename: str | None = None

    @field_validator("declared_sha256")
    @classmethod
    def _sha(cls, value: str) -> str:
        digest = value.strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("declared_sha256 must be 64 lowercase hex characters")
        return digest

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        if not name or "/" in name or "\\" in name or ".." in name:
            raise ValueError("filename must be a single safe path component")
        if not _SAFE_NAME_RE.fullmatch(name):
            raise ValueError("filename contains unsupported characters")
        return name


class UploadSession(BaseModel):
    upload_id: str
    declared_size: int
    declared_sha256: str
    media_type: str
    filename: str | None = None
    status: str = "pending"
    created_at: str
    finalized_at: str | None = None
    stored_path: str | None = None


@dataclass
class FinalizedUpload:
    upload_id: str
    path: Path
    sha256: str
    size_bytes: int
    media_type: str
    bundle_heads: list[str]


class UploadStore:
    """Server-side staging for untrusted git bundles until hash verification."""

    def __init__(self, root: Path, config: IngressConfig) -> None:
        self.root = root.resolve()
        self.config = config
        self.staging = self.root / "uploads" / "staging"
        self.final = self.root / "uploads" / "final"
        self.staging.mkdir(parents=True, exist_ok=True)
        self.final.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, UploadSession] = {}

    def preflight(self, body: UploadPreflight) -> UploadSession:
        if body.declared_size > self.config.max_upload_bytes:
            raise UnsafeOperationError(
                "Upload exceeds configured size bound",
                details={
                    "declared_size": body.declared_size,
                    "max_upload_bytes": self.config.max_upload_bytes,
                },
            )
        media = body.media_type.strip().lower()
        allowed = {m.lower() for m in self.config.allowed_upload_media_types}
        if media not in allowed:
            raise UnsafeOperationError(
                "Upload media type is not allowlisted",
                details={"media_type": body.media_type, "allowed": sorted(allowed)},
            )
        if (
            body.filename
            and len(body.filename.encode("utf-8")) > self.config.max_upload_filename_bytes
        ):
            raise UnsafeOperationError("Upload filename exceeds byte bound")
        upload_id = f"upl_{uuid.uuid4().hex}"
        session = UploadSession(
            upload_id=upload_id,
            declared_size=body.declared_size,
            declared_sha256=body.declared_sha256,
            media_type=media,
            filename=body.filename,
            created_at=datetime.now(UTC).isoformat(),
        )
        with self._lock:
            self._sessions[upload_id] = session
        return session

    def accept_bytes(self, upload_id: str, payload: bytes) -> UploadSession:
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is None:
                raise UnsafeOperationError("Unknown upload id")
            if session.status != "pending":
                raise UnsafeOperationError(
                    "Upload is not accepting bytes",
                    details={"status": session.status},
                )
        if len(payload) != session.declared_size:
            raise UnsafeOperationError(
                "Upload byte count does not match preflight",
                details={
                    "declared_size": session.declared_size,
                    "received_size": len(payload),
                },
            )
        if len(payload) > self.config.max_upload_bytes:
            raise UnsafeOperationError("Upload exceeds configured size bound")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != session.declared_sha256:
            raise UnsafeOperationError(
                "Upload digest mismatch",
                details={
                    "declared_sha256": session.declared_sha256,
                    "actual_sha256": digest,
                },
            )
        if not payload.startswith(_BUNDLE_MAGIC) or b"git bundle" not in payload[:64]:
            raise UnsafeOperationError(
                "Upload is not a git bundle",
                details={"media_type": session.media_type},
            )
        # Reject embedded path-like hostile markers before any git interaction.
        _assert_no_path_escape(payload)
        staging_path = self.staging / f"{upload_id}.bundle"
        staging_path.write_bytes(payload)
        with self._lock:
            session.status = "received"
            session.stored_path = str(staging_path)
            self._sessions[upload_id] = session
        return session

    def finalize(self, upload_id: str) -> FinalizedUpload:
        with self._lock:
            session = self._sessions.get(upload_id)
            if session is None:
                raise UnsafeOperationError("Unknown upload id")
            if session.status != "received":
                raise UnsafeOperationError(
                    "Upload must be received before finalize",
                    details={"status": session.status},
                )
            staging = Path(session.stored_path or "")
        if not staging.is_file():
            raise UnsafeOperationError("Staged upload missing")
        heads = _verify_git_bundle(staging)
        final_path = self.final / f"{session.declared_sha256}.bundle"
        if not final_path.exists():
            shutil.move(str(staging), str(final_path))
        else:
            staging.unlink(missing_ok=True)
        with self._lock:
            session.status = "finalized"
            session.finalized_at = datetime.now(UTC).isoformat()
            session.stored_path = str(final_path)
            self._sessions[upload_id] = session
        return FinalizedUpload(
            upload_id=upload_id,
            path=final_path,
            sha256=session.declared_sha256,
            size_bytes=session.declared_size,
            media_type=session.media_type,
            bundle_heads=heads,
        )

    def get(self, upload_id: str) -> UploadSession | None:
        with self._lock:
            return self._sessions.get(upload_id)


def _assert_no_path_escape(payload: bytes) -> None:
    """Fail closed on classic path-traversal markers in the scan window.

    Git bundles are binary and may contain NUL bytes; those are allowed. We only
    reject obvious absolute/parent path markers that appear in hostile archive
    pretenders before ``git bundle`` is invoked.
    """
    window = payload[: min(len(payload), 1_048_576)]
    # Only scan the textual preamble (until the first double-NUL / binary pack).
    text_end = window.find(b"\n\n")
    if text_end == -1:
        text_end = min(len(window), 4096)
    text = window[:text_end]
    for marker in (b"../", b"..\\", b"/etc/", b"C:\\", b"\\\\"):
        if marker in text:
            raise UnsafeOperationError(
                "Upload contains path-escape markers",
                details={"marker": marker.decode("latin-1")},
            )


def _verify_git_bundle(path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "bundle", "list-heads", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "bundle verify failed").strip()
        raise UnsafeOperationError(
            "Git bundle verification failed",
            details={"stderr": detail[-2000:]},
        )
    heads: list[str] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        ref = parts[-1] if parts else line
        if ".." in ref or ref.startswith("/") or "\\" in ref:
            raise UnsafeOperationError(
                "Git bundle ref escapes path bounds",
                details={"ref": ref},
            )
        heads.append(ref)
    return heads


def upload_bounds_summary(config: IngressConfig) -> dict[str, Any]:
    return {
        "max_upload_bytes": config.max_upload_bytes,
        "allowed_upload_media_types": list(config.allowed_upload_media_types),
        "max_upload_filename_bytes": config.max_upload_filename_bytes,
        "supported_upload_kinds": ["git_bundle"],
    }
