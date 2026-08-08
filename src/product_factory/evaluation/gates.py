"""Regression gates before changing pack/skill/model defaults (PMX)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from product_factory.evaluation.experiments import (
    ExperimentManifest,
    ScorecardMetrics,
    ScorecardRecord,
)


class GateThresholds(BaseModel):
    """Fail-closed thresholds for promoting a default change."""

    min_quality_score: float = 0.0
    max_unsupported_claim_rate: float = 1.0
    min_correct_unknown_escalation_rate: float = 0.0
    max_latency_ms: float | None = None
    max_cost_usd: float | None = None
    max_policy_violation_rate: float = 0.0
    require_corpus_sha256: bool = True


class GateResult(BaseModel):
    passed: bool
    experiment_id: str
    failures: list[str] = Field(default_factory=list)
    checked_scorecards: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


def _metric_failures(metrics: ScorecardMetrics, thresholds: GateThresholds) -> list[str]:
    failures: list[str] = []
    if metrics.quality_score is not None and metrics.quality_score < thresholds.min_quality_score:
        failures.append(f"quality_score {metrics.quality_score} < {thresholds.min_quality_score}")
    if (
        metrics.unsupported_claim_rate is not None
        and metrics.unsupported_claim_rate > thresholds.max_unsupported_claim_rate
    ):
        failures.append(
            "unsupported_claim_rate "
            f"{metrics.unsupported_claim_rate} > {thresholds.max_unsupported_claim_rate}"
        )
    if (
        metrics.correct_unknown_escalation_rate is not None
        and metrics.correct_unknown_escalation_rate < thresholds.min_correct_unknown_escalation_rate
    ):
        failures.append(
            "correct_unknown_escalation_rate "
            f"{metrics.correct_unknown_escalation_rate} < "
            f"{thresholds.min_correct_unknown_escalation_rate}"
        )
    if (
        thresholds.max_latency_ms is not None
        and metrics.latency_ms is not None
        and metrics.latency_ms > thresholds.max_latency_ms
    ):
        failures.append(f"latency_ms {metrics.latency_ms} > {thresholds.max_latency_ms}")
    if (
        thresholds.max_cost_usd is not None
        and metrics.cost_usd is not None
        and metrics.cost_usd > thresholds.max_cost_usd
    ):
        failures.append(f"cost_usd {metrics.cost_usd} > {thresholds.max_cost_usd}")
    if (
        metrics.policy_violation_rate is not None
        and metrics.policy_violation_rate > thresholds.max_policy_violation_rate
    ):
        failures.append(
            "policy_violation_rate "
            f"{metrics.policy_violation_rate} > {thresholds.max_policy_violation_rate}"
        )
    return failures


def evaluate_regression_gate(
    *,
    experiment: ExperimentManifest,
    scorecards: list[ScorecardRecord],
    thresholds: GateThresholds | None = None,
    expected_corpus_sha256: str | None = None,
) -> GateResult:
    """Block default changes unless scorecards meet thresholds on the pinned corpus."""
    thresholds = thresholds or GateThresholds()
    failures: list[str] = []
    if experiment.status not in {"active", "promoted", "draft", "deferred"}:
        failures.append(f"experiment status {experiment.status!r} is not eligible")

    corpus_sha = expected_corpus_sha256 or experiment.corpus_sha256
    if thresholds.require_corpus_sha256:
        if not corpus_sha:
            failures.append("corpus sha256 missing")
        mismatched = [
            s.scorecard_id for s in scorecards if s.corpus_sha256 and s.corpus_sha256 != corpus_sha
        ]
        if mismatched:
            failures.append(f"scorecard corpus mismatch: {', '.join(mismatched)}")

    relevant = [
        s
        for s in scorecards
        if s.corpus_id == experiment.corpus_id
        and (not s.corpus_sha256 or s.corpus_sha256 == corpus_sha)
    ]
    if not relevant:
        failures.append("no scorecards for experiment corpus")

    checked: list[str] = []
    for record in relevant:
        checked.append(record.scorecard_id)
        failures.extend(
            f"{record.scorecard_id}: {msg}" for msg in _metric_failures(record.metrics, thresholds)
        )

    return GateResult(
        passed=not failures,
        experiment_id=experiment.experiment_id,
        failures=failures,
        checked_scorecards=checked,
        details={
            "corpus_id": experiment.corpus_id,
            "corpus_sha256": corpus_sha,
            "thresholds": thresholds.model_dump(),
        },
    )
