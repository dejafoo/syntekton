"""Telemetry recorder — durable SQLite events + optional JSONL mirror."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from product_factory.observability.contracts import (
    CaptureLevel,
    ContentRef,
    EventSeverity,
    ObservabilityEvent,
)
from product_factory.observability.events import EventLog
from product_factory.observability.ids import new_event_id, new_span_id, new_trace_id
from product_factory.observability.redaction import capture_content, redact_value
from product_factory.persistence.database import Database

logger = logging.getLogger("product_factory.observability")


def capture_level_from_env() -> CaptureLevel:
    raw = os.environ.get("PRODUCT_FACTORY_CAPTURE_LEVEL", CaptureLevel.REDACTED.value)
    try:
        return CaptureLevel(raw.lower())
    except ValueError:
        return CaptureLevel.REDACTED


class TelemetryRecorder:
    """
    Injected into RunCoordinator / gateway / broker.

    Writes ObservabilityEvent rows to SQLite (authoritative) and optionally
    mirrors to per-run JSONL. Never raises into the orchestration path for
    transient SQLite busy; emits observability.degraded instead when possible.
    """

    def __init__(
        self,
        db: Database,
        *,
        jsonl: EventLog | None = None,
        content_dir: Path | None = None,
        capture_level: CaptureLevel | None = None,
        otel_exporter: Any | None = None,
    ) -> None:
        self.db = db
        self.jsonl = jsonl
        self.content_dir = content_dir
        if self.content_dir is not None:
            self.content_dir.mkdir(parents=True, exist_ok=True)
        self.capture_level = capture_level or capture_level_from_env()
        self.otel_exporter = otel_exporter
        self._trace_ids: dict[str, str] = {}
        self._run_spans: dict[str, str] = {}
        self._task_spans: dict[str, str] = {}
        self._degraded = False

    def ensure_trace(self, run_id: str) -> tuple[str, str]:
        if run_id not in self._trace_ids:
            self._trace_ids[run_id] = new_trace_id()
            self._run_spans[run_id] = new_span_id()
        return self._trace_ids[run_id], self._run_spans[run_id]

    def task_span(self, run_id: str, task_id: str) -> tuple[str, str, str]:
        trace_id, parent = self.ensure_trace(run_id)
        key = f"{run_id}:{task_id}"
        if key not in self._task_spans:
            self._task_spans[key] = new_span_id()
        return trace_id, self._task_spans[key], parent

    def emit(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        task_id: str | None = None,
        request_id: str | None = None,
        tool_call_id: str | None = None,
        summary: str = "",
        severity: EventSeverity = EventSeverity.INFO,
        content: Any | None = None,
        content_logical_name: str | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> ObservabilityEvent | None:
        trace_id, run_span = self.ensure_trace(run_id)
        if task_id and span_id is None:
            trace_id, span_id, parent_span_id = self.task_span(run_id, task_id)
        elif span_id is None:
            span_id = run_span

        safe_payload = redact_value(payload or {})
        content_refs: list[ContentRef] = []
        if content is not None and self.capture_level != CaptureLevel.OFF:
            ref, body = capture_content(
                content,
                level=self.capture_level,
                logical_name=content_logical_name,
            )
            if ref is not None:
                content_refs.append(ref)
                if body is not None and self.content_dir is not None:
                    path = self.content_dir / ref.sha256
                    if not path.exists():
                        path.write_text(
                            json.dumps(body, indent=2, default=str)
                            if not isinstance(body, str)
                            else body,
                            encoding="utf-8",
                        )

        event = ObservabilityEvent(
            event_id=new_event_id(),
            type=event_type,
            run_id=run_id,
            task_id=task_id,
            request_id=request_id,
            tool_call_id=tool_call_id,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            severity=severity,
            summary=summary or event_type,
            payload=safe_payload,
            content_refs=content_refs,
        )
        try:
            seq = self.db.append_event(event)
            event.seq = seq
        except sqlite3.OperationalError as exc:
            self._degraded = True
            logger.warning("observability write failed: %s", exc)
            with contextlib.suppress(Exception):
                self.db.append_event(
                    ObservabilityEvent(
                        event_id=new_event_id(),
                        type="observability.degraded",
                        run_id=run_id,
                        severity=EventSeverity.WARNING,
                        summary="Event write failed",
                        payload={"error": str(exc), "dropped_type": event_type},
                        trace_id=trace_id,
                        span_id=span_id,
                    )
                )
            return None

        if self.jsonl is not None:
            try:
                self.jsonl.emit(run_id, event_type, safe_payload)
            except OSError as exc:
                logger.warning("jsonl mirror failed: %s", exc)

        if self.otel_exporter is not None:
            try:
                self.otel_exporter.on_event(event)
            except Exception as exc:  # noqa: BLE001 — exporter must not break runs
                logger.debug("otel export failed: %s", exc)

        return event

    @property
    def degraded(self) -> bool:
        return self._degraded
