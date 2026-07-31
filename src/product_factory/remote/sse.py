"""Cursor-resumable SSE consumer with poll-status fallback (PM2.B2)."""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import httpx

from product_factory.host.protocol import HostResponse
from product_factory.host.service import TERMINAL_STATUSES

if TYPE_CHECKING:
    from product_factory.remote.client import RemotePfClient

_DEFAULT_WANTED = TERMINAL_STATUSES | {"awaiting_approval"}


def _parse_sse_chunk(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Parse complete SSE events from a text buffer; return (events, remainder)."""
    events: list[dict[str, Any]] = []
    parts = buffer.split("\n\n")
    remainder = parts.pop() if parts else ""
    for block in parts:
        if not block.strip():
            continue
        event_type = "message"
        data_lines: list[str] = []
        event_id: str | None = None
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.startswith("id:"):
                event_id = line[3:].strip()
        raw = "\n".join(data_lines).strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        if isinstance(payload, dict):
            if event_id is not None and "seq" not in payload:
                with contextlib.suppress(ValueError):
                    payload["seq"] = int(event_id)
            payload.setdefault("type", event_type)
            events.append(payload)
    return events, remainder


def iter_sse_events(
    client: httpx.Client,
    run_id: str,
    *,
    after_seq: int = 0,
    live: bool = True,
    headers: dict[str, str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield SSE event payloads, deduplicating by seq and advancing the cursor."""
    url = f"/api/v1/runs/{run_id}/events/stream"
    params = {"after_seq": after_seq, "live": "true" if live else "false"}
    seen: set[int] = set()
    with client.stream("GET", url, params=params, headers=headers or {}) as response:
        if response.status_code == 401:
            raise RuntimeError("Unauthorized: missing or invalid bearer token")
        response.raise_for_status()
        buffer = ""
        for chunk in response.iter_text():
            buffer += chunk
            events, buffer = _parse_sse_chunk(buffer)
            for event in events:
                seq = event.get("seq")
                if isinstance(seq, int):
                    if seq in seen:
                        continue
                    seen.add(seq)
                yield event


def wait_for_terminal(
    client: RemotePfClient,
    run_id: str,
    *,
    after_seq: int = 0,
    timeout: float = 600.0,
    poll_interval: float = 0.5,
    wanted: set[str] | None = None,
) -> HostResponse:
    """Prefer SSE; fall back to status polling on stream failure."""
    targets = wanted or set(_DEFAULT_WANTED)
    deadline = time.monotonic() + timeout
    cursor = after_seq

    def _status_if_ready() -> HostResponse | None:
        status = client.status(run_id)
        if status.status in targets:
            return status
        return None

    ready = _status_if_ready()
    if ready is not None:
        return ready

    try:
        for event in client.iter_sse(run_id, after_seq=cursor, live=True):
            seq = event.get("seq")
            if isinstance(seq, int):
                cursor = max(cursor, seq)
            ready = _status_if_ready()
            if ready is not None:
                return ready
            if time.monotonic() >= deadline:
                break
    except Exception:
        # SSE unavailable / disconnected — poll status until timeout.
        pass

    while time.monotonic() < deadline:
        ready = _status_if_ready()
        if ready is not None:
            return ready
        time.sleep(poll_interval)

    last = client.status(run_id)
    if last.status in targets:
        return last
    return HostResponse.failure(
        code="wait_timeout",
        message=f"Timed out waiting for run {run_id} to reach {sorted(targets)}",
        run_id=run_id,
        status=last.status,
        details={"after_seq": cursor, "last_status": last.status},
    )
