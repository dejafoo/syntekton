"""Circuit breaker for unhealthy local model endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

CircuitState = Literal["closed", "open", "half_open"]


@dataclass
class CircuitBreaker:
    """Fail-closed local-route breaker with timed recovery.

    Open circuits skip the local endpoint so fallback budget is not burned by a
    flapping service. Half-open admits a single trial after ``recovery_timeout_s``.
    """

    failure_threshold: int = 3
    recovery_timeout_s: float = 60.0
    half_open_max_calls: int = 1
    clock: Callable[[], float] = field(default=time.monotonic)

    state: CircuitState = "closed"
    failure_count: int = 0
    opened_at: float | None = None
    _half_open_calls: int = field(default=0, repr=False)

    def allow_request(self) -> bool:
        """Return True when a local attempt (or probe) may proceed."""
        if self.state == "closed":
            return True
        if self.state == "open":
            assert self.opened_at is not None
            if self.clock() - self.opened_at >= self.recovery_timeout_s:
                self.state = "half_open"
                self._half_open_calls = 0
                return self._admit_half_open()
            return False
        return self._admit_half_open()

    def record_success(self) -> None:
        self.failure_count = 0
        self._half_open_calls = 0
        self.opened_at = None
        self.state = "closed"

    def record_failure(self) -> None:
        self.failure_count += 1
        if self.state == "half_open" or self.failure_count >= self.failure_threshold:
            self.state = "open"
            self.opened_at = self.clock()
            self._half_open_calls = 0

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "opened_at": self.opened_at,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_s": self.recovery_timeout_s,
        }

    def _admit_half_open(self) -> bool:
        if self._half_open_calls >= self.half_open_max_calls:
            return False
        self._half_open_calls += 1
        return True
