"""W3C-compatible trace/span ID helpers."""

from __future__ import annotations

import secrets


def new_trace_id() -> str:
    """32 lowercase hex characters (W3C trace-id)."""
    return secrets.token_hex(16)


def new_span_id() -> str:
    """16 lowercase hex characters (W3C parent-id / span-id)."""
    return secrets.token_hex(8)


def new_event_id() -> str:
    return f"evt-{secrets.token_hex(8)}"
