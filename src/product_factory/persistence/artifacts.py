"""Content-addressed artifact store with crash-safe atomic writes (SD3.C)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from product_factory.domain.artifacts import ArtifactRef
from product_factory.domain.errors import UnsafeOperationError


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.blobs = root / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)

    def put_bytes(
        self,
        content: bytes,
        *,
        media_type: str,
        logical_name: str,
        created_by_task_id: str,
        trust_level: str = "generated",
        created_by_tool_call_id: str | None = None,
        schema_id: str | None = None,
        schema_version: str | None = None,
        handoff_state: str | None = None,
    ) -> ArtifactRef:
        sha = hashlib.sha256(content).hexdigest()
        path = self.blobs / sha
        if path.exists():
            self.verify_blob(sha, expected_size=len(content))
        else:
            self._atomic_write(path, content, expected_sha256=sha)
        rel = f"blobs/{sha}"
        return ArtifactRef(
            sha256=sha,
            media_type=media_type,
            size_bytes=len(content),
            logical_name=logical_name,
            relative_path=rel,
            created_by_task_id=created_by_task_id,
            created_by_tool_call_id=created_by_tool_call_id,
            trust_level=trust_level,  # type: ignore[arg-type]
            schema_id=schema_id,
            schema_version=schema_version,
            handoff_state=handoff_state,  # type: ignore[arg-type]
        )

    def put_text(
        self,
        text: str,
        *,
        media_type: str,
        logical_name: str,
        created_by_task_id: str,
        trust_level: str = "generated",
        created_by_tool_call_id: str | None = None,
        schema_id: str | None = None,
        schema_version: str | None = None,
        handoff_state: str | None = None,
    ) -> ArtifactRef:
        return self.put_bytes(
            text.encode("utf-8"),
            media_type=media_type,
            logical_name=logical_name,
            created_by_task_id=created_by_task_id,
            trust_level=trust_level,
            created_by_tool_call_id=created_by_tool_call_id,
            schema_id=schema_id,
            schema_version=schema_version,
            handoff_state=handoff_state,
        )

    def put_json(
        self,
        data: Any,
        *,
        logical_name: str,
        created_by_task_id: str,
        created_by_tool_call_id: str | None = None,
        schema_id: str | None = None,
        schema_version: str | None = None,
        trust_level: str = "generated",
        handoff_state: str | None = None,
    ) -> ArtifactRef:
        body = json.dumps(data, indent=2, default=str, sort_keys=True) + "\n"
        return self.put_text(
            body,
            media_type="application/json",
            logical_name=logical_name,
            created_by_task_id=created_by_task_id,
            trust_level=trust_level,
            created_by_tool_call_id=created_by_tool_call_id,
            schema_id=schema_id,
            schema_version=schema_version,
            handoff_state=handoff_state,
        )

    def get_bytes(self, sha256: str, *, verify: bool = False) -> bytes:
        path = self.blobs / sha256
        if not path.exists():
            raise FileNotFoundError(sha256)
        data = path.read_bytes()
        if verify:
            self.verify_blob(sha256, expected_size=len(data), content=data)
        return data

    def get_text(self, sha256: str, *, verify: bool = False) -> str:
        return self.get_bytes(sha256, verify=verify).decode("utf-8")

    def exists(self, sha256: str) -> bool:
        return (self.blobs / sha256).exists()

    def verify_blob(
        self,
        sha256: str,
        *,
        expected_size: int | None = None,
        content: bytes | None = None,
    ) -> None:
        path = self.blobs / sha256
        if not path.exists():
            raise FileNotFoundError(sha256)
        data = content if content is not None else path.read_bytes()
        if expected_size is not None and len(data) != expected_size:
            raise UnsafeOperationError(
                "Artifact blob size mismatch",
                details={"sha256": sha256, "expected": expected_size, "actual": len(data)},
            )
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha256:
            raise UnsafeOperationError(
                "Artifact blob digest mismatch",
                details={"sha256": sha256, "actual": actual},
            )

    def _atomic_write(self, final_path: Path, content: bytes, *, expected_sha256: str) -> None:
        """Write via same-filesystem temp file, fsync, verify digest, then rename."""
        final_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{final_path.name}.",
            suffix=".tmp",
            dir=str(final_path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            written = tmp_path.read_bytes()
            actual = hashlib.sha256(written).hexdigest()
            if actual != expected_sha256:
                raise UnsafeOperationError(
                    "Artifact temp digest mismatch before rename",
                    details={"expected": expected_sha256, "actual": actual},
                )
            if len(written) != len(content):
                raise UnsafeOperationError(
                    "Artifact temp size mismatch before rename",
                    details={"expected": len(content), "actual": len(written)},
                )
            os.replace(tmp_path, final_path)
            try:
                dir_fd = os.open(str(final_path.parent), os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        finally:
            if tmp_path.exists():
                with contextlib.suppress(OSError):
                    tmp_path.unlink()
