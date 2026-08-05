"""Durable operational evidence for local-route admission decisions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalRouteEvidenceStore:
    """Write measured probe/admission snapshots under the data root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.directory = self.root / "ops" / "local_route_admission"
        self.directory.mkdir(parents=True, exist_ok=True)

    def record(self, payload: dict[str, Any]) -> Path:
        profile = str(payload.get("profile") or "unknown")
        path = self.directory / f"{profile}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        latest = self.directory / "latest.json"
        latest.write_text(
            json.dumps(
                {
                    "schema_version": "local_route_admission.v1",
                    "profile": profile,
                    "path": str(path),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def read(self, profile: str) -> dict[str, Any] | None:
        path = self.directory / f"{profile}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
