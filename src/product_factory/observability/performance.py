"""Hermetic orchestration stage timing (SD8 scaffolding).

Baselines and correlation IDs only. Production tuning is deferred until SD6 G4
operational proof exists; do not treat these numbers as AMD performance wins.
"""

from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

StageName = Literal[
    "plan",
    "inventory",
    "prompt",
    "model_wait",
    "tools",
    "validation",
    "sqlite",
    "sse",
    "queue",
]

KNOWN_STAGES: tuple[StageName, ...] = (
    "plan",
    "inventory",
    "prompt",
    "model_wait",
    "tools",
    "validation",
    "sqlite",
    "sse",
    "queue",
)

MEASUREMENT_GLOSSARY: dict[str, str] = {
    "plan": "Plan compilation / deterministic planner wall time",
    "inventory": "SafeRepositoryInventory scan wall time",
    "prompt": "Prompt/context assembly wall time",
    "model_wait": "Time waiting on model/provider queue or response",
    "tools": "Tool/connector invocation wall time",
    "validation": "Validator and repair eligibility checks",
    "sqlite": "SQLite transaction / projection query wall time",
    "sse": "SSE event emit/batch delay",
    "queue": "Local worker/admission queue wait",
    "correlation_id": "Per-run opaque id shared across stage samples",
    "p50": "Median of hermetic fixture samples",
    "p95": "95th percentile of hermetic fixture samples",
}


@dataclass(slots=True)
class StageSample:
    stage: StageName
    duration_ms: float
    correlation_id: str
    fixture_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StageStats:
    stage: StageName
    count: int
    p50_ms: float
    p95_ms: float
    mean_ms: float


@dataclass(slots=True)
class MeasurementSession:
    """Collects stage timings under one correlation id."""

    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    fixture_id: str | None = None
    samples: list[StageSample] = field(default_factory=list)

    @contextmanager
    def measure(self, stage: StageName, **metadata: Any) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.samples.append(
                StageSample(
                    stage=stage,
                    duration_ms=elapsed_ms,
                    correlation_id=self.correlation_id,
                    fixture_id=self.fixture_id,
                    metadata=dict(metadata),
                )
            )

    def record(self, stage: StageName, duration_ms: float, **metadata: Any) -> StageSample:
        sample = StageSample(
            stage=stage,
            duration_ms=float(duration_ms),
            correlation_id=self.correlation_id,
            fixture_id=self.fixture_id,
            metadata=dict(metadata),
        )
        self.samples.append(sample)
        return sample

    def stats_by_stage(self) -> dict[StageName, StageStats]:
        by_stage: dict[StageName, list[float]] = {stage: [] for stage in KNOWN_STAGES}
        for sample in self.samples:
            by_stage[sample.stage].append(sample.duration_ms)
        out: dict[StageName, StageStats] = {}
        for stage, values in by_stage.items():
            if not values:
                continue
            ordered = sorted(values)
            out[stage] = StageStats(
                stage=stage,
                count=len(ordered),
                p50_ms=_percentile(ordered, 50),
                p95_ms=_percentile(ordered, 95),
                mean_ms=float(statistics.fmean(ordered)),
            )
        return out

    def as_payload(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "fixture_id": self.fixture_id,
            "samples": [
                {
                    "stage": sample.stage,
                    "duration_ms": round(sample.duration_ms, 4),
                    "correlation_id": sample.correlation_id,
                    "fixture_id": sample.fixture_id,
                    "metadata": sample.metadata,
                }
                for sample in self.samples
            ],
            "stats": {
                stage: {
                    "count": stats.count,
                    "p50_ms": round(stats.p50_ms, 4),
                    "p95_ms": round(stats.p95_ms, 4),
                    "mean_ms": round(stats.mean_ms, 4),
                }
                for stage, stats in self.stats_by_stage().items()
            },
            "glossary": MEASUREMENT_GLOSSARY,
            "honesty": (
                "Baselines recorded; tuning deferred pending G4 operational proof"
            ),
        }


def _percentile(ordered: list[float], percentile: int) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (percentile / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return float(ordered[low] * (1.0 - weight) + ordered[high] * weight)


def synthesize_baseline_samples(
    *,
    fixture_id: str,
    stage_durations_ms: Mapping[StageName, list[float]],
    correlation_id: str | None = None,
) -> MeasurementSession:
    """Build a hermetic baseline session from synthetic stage durations."""
    session = MeasurementSession(
        correlation_id=correlation_id or f"baseline-{fixture_id}-{uuid.uuid4().hex[:8]}",
        fixture_id=fixture_id,
    )
    for stage, values in stage_durations_ms.items():
        for value in values:
            session.record(stage, value)
    return session


__all__ = [
    "KNOWN_STAGES",
    "MEASUREMENT_GLOSSARY",
    "MeasurementSession",
    "StageName",
    "StageSample",
    "StageStats",
    "synthesize_baseline_samples",
]
