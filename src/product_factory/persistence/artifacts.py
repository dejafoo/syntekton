"""Content-addressed artifact store."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from product_factory.domain.artifacts import ArtifactRef


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
    ) -> ArtifactRef:
        sha = hashlib.sha256(content).hexdigest()
        path = self.blobs / sha
        if not path.exists():
            path.write_bytes(content)
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
        )

    def put_text(
        self,
        text: str,
        *,
        media_type: str,
        logical_name: str,
        created_by_task_id: str,
        trust_level: str = "generated",
    ) -> ArtifactRef:
        return self.put_bytes(
            text.encode("utf-8"),
            media_type=media_type,
            logical_name=logical_name,
            created_by_task_id=created_by_task_id,
            trust_level=trust_level,
        )

    def put_json(
        self,
        data: Any,
        *,
        logical_name: str,
        created_by_task_id: str,
    ) -> ArtifactRef:
        body = json.dumps(data, indent=2, default=str, sort_keys=True) + "\n"
        return self.put_text(
            body,
            media_type="application/json",
            logical_name=logical_name,
            created_by_task_id=created_by_task_id,
            trust_level="generated",
        )

    def get_bytes(self, sha256: str) -> bytes:
        path = self.blobs / sha256
        if not path.exists():
            raise FileNotFoundError(sha256)
        return path.read_bytes()

    def get_text(self, sha256: str) -> str:
        return self.get_bytes(sha256).decode("utf-8")

    def exists(self, sha256: str) -> bool:
        return (self.blobs / sha256).exists()
