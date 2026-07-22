"""Shared event streaming helpers for WebSocket and SSE."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from product_factory.observability.query import ObservabilityQueryService

HEARTBEAT_SECONDS = 15.0
POLL_SECONDS = 0.35
DEFAULT_BATCH = 100
MAX_QUEUE = 256


async def iter_events(
    query: ObservabilityQueryService,
    *,
    run_id: str | None,
    after_seq: int = 0,
    types: list[str] | None = None,
    live: bool = True,
) -> AsyncIterator[dict[str, Any]]:
    """
    Yield event dicts after `after_seq`, then optionally poll for live updates.
    At-least-once delivery; clients should dedupe by event_id.
    """
    cursor = after_seq
    idle_ticks = 0
    while True:
        batch = await asyncio.to_thread(
            query.list_events,
            run_id=run_id,
            after_seq=cursor,
            limit=DEFAULT_BATCH,
            types=types,
        )
        if batch:
            idle_ticks = 0
            for event in batch:
                cursor = int(event["seq"])
                yield event
            continue
        if not live:
            return
        idle_ticks += 1
        if idle_ticks * POLL_SECONDS >= HEARTBEAT_SECONDS:
            idle_ticks = 0
            yield {
                "type": "heartbeat",
                "seq": cursor,
                "event_id": f"hb-{cursor}",
                "run_id": run_id,
                "summary": "heartbeat",
                "payload": {},
            }
        await asyncio.sleep(POLL_SECONDS)


def encode_sse(event: dict[str, Any]) -> str:
    event_id = event.get("seq") or event.get("event_id") or ""
    event_type = event.get("type") or "event"
    data = json.dumps(event, default=str)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"
