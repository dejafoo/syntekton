"""Append-only JSONL event log (optional diagnostic mirror)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class EventLog:
    """Per-run JSONL mirror. Durable authority is SQLite EventStore."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def emit(self, run_id: str, event_type: str, payload: dict[str, Any] | None = None) -> str:
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        record = {
            "event_id": event_id,
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": event_type,
            "payload": payload or {},
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return event_id

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
