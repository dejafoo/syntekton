"""Derive liveness / stuck state from progress timestamps."""

from __future__ import annotations

from datetime import UTC, datetime

from product_factory.observability.contracts import Liveness

# Thresholds in seconds
SLOW_AFTER_S = 60
SUSPECTED_STUCK_AFTER_S = 180
TIMED_OUT_AFTER_S = 600


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def derive_liveness(
    *,
    status: str,
    last_progress_at: str | None,
    now: datetime | None = None,
    slow_after_s: int = SLOW_AFTER_S,
    stuck_after_s: int = SUSPECTED_STUCK_AFTER_S,
    timeout_after_s: int = TIMED_OUT_AFTER_S,
) -> Liveness:
    terminal = {
        "completed",
        "failed",
        "blocked",
        "awaiting_approval",
        "plan_rejected",
        "budget_exhausted",
        "success",
        "skipped",
    }
    if status in terminal:
        return Liveness.HEALTHY
    ts = _parse_ts(last_progress_at)
    if ts is None:
        return Liveness.SUSPECTED_STUCK
    current = now or datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age = (current - ts).total_seconds()
    if age >= timeout_after_s:
        return Liveness.TIMED_OUT
    if age >= stuck_after_s:
        return Liveness.SUSPECTED_STUCK
    if age >= slow_after_s:
        return Liveness.SLOW
    return Liveness.HEALTHY
