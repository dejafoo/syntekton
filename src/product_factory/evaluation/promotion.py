"""SD6 comparison arms and local-first / skill promotion gates.

Authoritative thresholds live in ``config/evaluation/sd6_promotion.yaml``.
Gate functions fail closed: missing metrics or unresolved reliability issues
block promotion rather than inventing success.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

ComparisonArm = Literal[
    "local_only",
    "local_first_fallback",
    "cloud",
    "single_agent_baseline",
    "skills_enabled",
    "skills_disabled",
]

COMPARISON_ARMS: tuple[ComparisonArm, ...] = (
    "local_only",
    "local_first_fallback",
    "cloud",
    "single_agent_baseline",
    "skills_enabled",
    "skills_disabled",
)

PromotionDecisionKind = Literal["promote", "no_promote", "deferred", "rollback"]


class ArmMetrics(BaseModel):
    """Comparable arm metrics for SD6 promotion decisions."""

    arm: ComparisonArm
    policy_violation_rate: float | None = None
    accepted_outcome_rate: float | None = None
    validator_pass_rate: float | None = None
    human_correction_effort: float | None = None
    unsupported_claim_rate: float | None = None
    cloud_spend_usd: float | None = None
    quality_score: float | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None
    latency_tradeoff_documented: bool = False
    unresolved_reliability_regression: bool = False
    fallback_policy_id: str | None = None
    category_accepted_outcome_rates: dict[str, float] = Field(default_factory=dict)
    case_count: int = 0
    seed_count: int = 0
    notes: str = ""


class LocalFirstThresholds(BaseModel):
    max_policy_violation_rate: float = 0.0
    max_accepted_outcome_deficit_pp: float = 5.0
    max_correction_effort_worsening_pct: float = 10.0
    max_unsupported_claim_deficit_pp: float = 2.0
    min_cloud_spend_reduction_pct: float = 30.0
    require_documented_latency_tradeoff: bool = True
    disallow_unresolved_reliability_regression: bool = True


class SkillPromotionThresholds(BaseModel):
    min_quality_improvement_pp: float = 5.0
    min_correction_effort_reduction_pct: float = 10.0
    max_policy_violation_rate: float = 0.0
    max_cost_or_latency_increase_pct: float = 20.0


class FailClosedRules(BaseModel):
    mask_safety_with_aggregate: bool = False
    allow_weaker_category: bool = False
    allow_fallback_policy_mismatch: bool = False


class Sd6PromotionConfig(BaseModel):
    schema_version: str = "sd6.promotion.v1"
    harness_id: str = "product-factory-eval"
    min_cases_for_promotion: int = 30
    min_seeds_for_promotion: int = 3
    foundation_case_count: int = 12
    arms: list[ComparisonArm] = Field(default_factory=lambda: list(COMPARISON_ARMS))
    local_first_default: LocalFirstThresholds = Field(default_factory=LocalFirstThresholds)
    skill_promotion: SkillPromotionThresholds = Field(default_factory=SkillPromotionThresholds)
    fail_closed: FailClosedRules = Field(default_factory=FailClosedRules)
    external_adapters: dict[str, Any] = Field(default_factory=dict)


class PromotionGateResult(BaseModel):
    passed: bool
    decision: PromotionDecisionKind
    failures: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class PromotionRecord(BaseModel):
    """Durable promote / no-promote / deferred / rollback decision."""

    record_id: str
    experiment_id: str
    decision: PromotionDecisionKind
    candidate_arm: ComparisonArm
    baseline_arm: ComparisonArm | None = None
    corpus_id: str
    corpus_sha256: str
    harness_version: str
    scorecard_ids: list[str] = Field(default_factory=list)
    gate_failures: list[str] = Field(default_factory=list)
    rationale: str = ""
    reviewer: str = "hermetic"
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RollbackRecord(BaseModel):
    record_id: str
    promotion_record_id: str
    reason: str
    corpus_sha256: str
    harness_version: str
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


def default_promotion_config_path(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / "config" / "evaluation" / "sd6_promotion.yaml"


def load_sd6_promotion_config(path: Path | None = None) -> Sd6PromotionConfig:
    config_path = path or default_promotion_config_path()
    if not config_path.is_file():
        return Sd6PromotionConfig()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"SD6 promotion config must be a mapping: {config_path}")
    return Sd6PromotionConfig.model_validate(raw)


def _pp_deficit(candidate: float, baseline: float) -> float:
    """Percentage-point deficit of candidate versus baseline (positive = worse)."""
    return (baseline - candidate) * 100.0


def _pct_worsening(candidate: float, baseline: float) -> float:
    """Percent worsening of candidate versus baseline (positive = worse)."""
    if baseline == 0:
        return 0.0 if candidate == 0 else float("inf")
    return ((candidate - baseline) / baseline) * 100.0


def _pct_reduction(candidate: float, baseline: float) -> float:
    """Percent reduction of candidate versus baseline (positive = lower spend)."""
    if baseline == 0:
        return 0.0 if candidate == 0 else float("-inf")
    return ((baseline - candidate) / baseline) * 100.0


def _require(metric: float | None, label: str, failures: list[str]) -> float | None:
    if metric is None:
        failures.append(f"missing metric: {label}")
        return None
    return metric


def evaluate_local_first_promotion(
    *,
    candidate: ArmMetrics,
    cloud: ArmMetrics,
    config: Sd6PromotionConfig | None = None,
    operational_ready: bool = False,
) -> PromotionGateResult:
    """Enforce SD6 local-first default promotion rules against the cloud arm."""
    config = config or Sd6PromotionConfig()
    thresholds = config.local_first_default
    failures: list[str] = []
    details: dict[str, Any] = {
        "candidate_arm": candidate.arm,
        "cloud_arm": cloud.arm,
        "operational_ready": operational_ready,
    }

    if candidate.arm not in {"local_only", "local_first_fallback"}:
        failures.append(f"candidate arm {candidate.arm!r} is not a local-first arm")
    if cloud.arm != "cloud":
        failures.append(f"baseline arm {cloud.arm!r} must be 'cloud'")

    if not operational_ready:
        return PromotionGateResult(
            passed=False,
            decision="deferred",
            failures=[
                "operational AMD-owned multi-seed promotion run not available; "
                "local-first default remains deferred"
            ],
            details=details,
        )

    if candidate.case_count < config.min_cases_for_promotion:
        failures.append(
            f"case_count {candidate.case_count} < min_cases_for_promotion "
            f"{config.min_cases_for_promotion}"
        )
    if candidate.seed_count < config.min_seeds_for_promotion:
        failures.append(
            f"seed_count {candidate.seed_count} < min_seeds_for_promotion "
            f"{config.min_seeds_for_promotion}"
        )

    cand_policy = _require(
        candidate.policy_violation_rate, "candidate.policy_violation_rate", failures
    )
    if cand_policy is not None and cand_policy > thresholds.max_policy_violation_rate:
        failures.append(
            f"policy_violation_rate {cand_policy} > {thresholds.max_policy_violation_rate}"
        )

    cand_accept = _require(
        candidate.accepted_outcome_rate, "candidate.accepted_outcome_rate", failures
    )
    cloud_accept = _require(cloud.accepted_outcome_rate, "cloud.accepted_outcome_rate", failures)
    if cand_accept is not None and cloud_accept is not None:
        deficit = _pp_deficit(cand_accept, cloud_accept)
        details["accepted_outcome_deficit_pp"] = deficit
        if deficit > thresholds.max_accepted_outcome_deficit_pp:
            failures.append(
                f"accepted_outcome deficit {deficit:.2f}pp > "
                f"{thresholds.max_accepted_outcome_deficit_pp}pp"
            )

    cand_effort = _require(
        candidate.human_correction_effort, "candidate.human_correction_effort", failures
    )
    cloud_effort = _require(
        cloud.human_correction_effort, "cloud.human_correction_effort", failures
    )
    if cand_effort is not None and cloud_effort is not None:
        worsening = _pct_worsening(cand_effort, cloud_effort)
        details["correction_effort_worsening_pct"] = worsening
        if worsening > thresholds.max_correction_effort_worsening_pct:
            failures.append(
                f"correction effort worsening {worsening:.2f}% > "
                f"{thresholds.max_correction_effort_worsening_pct}%"
            )

    cand_unsupported = _require(
        candidate.unsupported_claim_rate, "candidate.unsupported_claim_rate", failures
    )
    cloud_unsupported = _require(
        cloud.unsupported_claim_rate, "cloud.unsupported_claim_rate", failures
    )
    if cand_unsupported is not None and cloud_unsupported is not None:
        # Higher unsupported rate is worse; deficit in "goodness" = candidate - cloud in pp.
        worse_pp = (cand_unsupported - cloud_unsupported) * 100.0
        details["unsupported_claim_worse_pp"] = worse_pp
        if worse_pp > thresholds.max_unsupported_claim_deficit_pp:
            failures.append(
                f"unsupported_claim rate worse by {worse_pp:.2f}pp > "
                f"{thresholds.max_unsupported_claim_deficit_pp}pp"
            )

    cand_spend = _require(candidate.cloud_spend_usd, "candidate.cloud_spend_usd", failures)
    cloud_spend = _require(cloud.cloud_spend_usd, "cloud.cloud_spend_usd", failures)
    if cand_spend is not None and cloud_spend is not None:
        reduction = _pct_reduction(cand_spend, cloud_spend)
        details["cloud_spend_reduction_pct"] = reduction
        if reduction < thresholds.min_cloud_spend_reduction_pct:
            failures.append(
                f"cloud spend reduction {reduction:.2f}% < "
                f"{thresholds.min_cloud_spend_reduction_pct}%"
            )

    if thresholds.require_documented_latency_tradeoff and not candidate.latency_tradeoff_documented:
        failures.append("latency trade-off not documented")
    if (
        thresholds.disallow_unresolved_reliability_regression
        and candidate.unresolved_reliability_regression
    ):
        failures.append("unresolved timeout/reliability regression")

    if not config.fail_closed.allow_fallback_policy_mismatch:
        if candidate.fallback_policy_id and cloud.fallback_policy_id:
            if candidate.fallback_policy_id != cloud.fallback_policy_id:
                # Local-first and cloud arms may differ by definition; require pre-registration
                # equality only when comparing skills or same protocol family.
                details["fallback_policy_note"] = (
                    f"candidate={candidate.fallback_policy_id} cloud={cloud.fallback_policy_id}"
                )
        if (
            candidate.fallback_policy_id
            and cloud.fallback_policy_id
            and candidate.arm == cloud.arm
            and candidate.fallback_policy_id != cloud.fallback_policy_id
        ):
            failures.append("fallback policy mismatch versus pre-registered protocol")

    if not config.fail_closed.allow_weaker_category:
        for category, cloud_rate in cloud.category_accepted_outcome_rates.items():
            cand_rate = candidate.category_accepted_outcome_rates.get(category)
            if cand_rate is None:
                failures.append(f"missing category rate for {category}")
                continue
            cat_deficit = _pp_deficit(cand_rate, cloud_rate)
            if cat_deficit > thresholds.max_accepted_outcome_deficit_pp:
                failures.append(f"category {category} accepted_outcome deficit {cat_deficit:.2f}pp")

    if (
        cand_policy is not None
        and cand_policy > 0
        and not config.fail_closed.mask_safety_with_aggregate
    ):
        # Already covered by max_policy_violation_rate == 0, keep explicit.
        pass

    return PromotionGateResult(
        passed=not failures,
        decision="promote" if not failures else "no_promote",
        failures=failures,
        details=details,
    )


def evaluate_skill_promotion(
    *,
    skills_enabled: ArmMetrics,
    skills_disabled: ArmMetrics,
    config: Sd6PromotionConfig | None = None,
    operational_ready: bool = False,
) -> PromotionGateResult:
    """Enforce SD6 individual-skill promotion rules versus the no-skill baseline."""
    config = config or Sd6PromotionConfig()
    thresholds = config.skill_promotion
    failures: list[str] = []
    details: dict[str, Any] = {"operational_ready": operational_ready}

    if skills_enabled.arm != "skills_enabled":
        failures.append(f"skills_enabled arm got {skills_enabled.arm!r}")
    if skills_disabled.arm != "skills_disabled":
        failures.append(f"skills_disabled arm got {skills_disabled.arm!r}")

    if not operational_ready:
        return PromotionGateResult(
            passed=False,
            decision="deferred",
            failures=[
                "operational skill scorecard run not available; skill promotion remains deferred"
            ],
            details=details,
        )

    enabled_policy = _require(
        skills_enabled.policy_violation_rate, "skills_enabled.policy_violation_rate", failures
    )
    disabled_policy = _require(
        skills_disabled.policy_violation_rate, "skills_disabled.policy_violation_rate", failures
    )
    if enabled_policy is not None and enabled_policy > thresholds.max_policy_violation_rate:
        failures.append(
            f"policy_violation_rate {enabled_policy} > {thresholds.max_policy_violation_rate}"
        )
    if (
        enabled_policy is not None
        and disabled_policy is not None
        and enabled_policy > disabled_policy
    ):
        failures.append("policy regression versus skills-disabled baseline")

    enabled_quality = _require(
        skills_enabled.quality_score, "skills_enabled.quality_score", failures
    )
    disabled_quality = _require(
        skills_disabled.quality_score, "skills_disabled.quality_score", failures
    )
    quality_gain_pp: float | None = None
    if enabled_quality is not None and disabled_quality is not None:
        quality_gain_pp = (enabled_quality - disabled_quality) * 100.0
        details["quality_improvement_pp"] = quality_gain_pp

    enabled_effort = _require(
        skills_enabled.human_correction_effort, "skills_enabled.human_correction_effort", failures
    )
    disabled_effort = _require(
        skills_disabled.human_correction_effort, "skills_disabled.human_correction_effort", failures
    )
    effort_reduction_pct: float | None = None
    if enabled_effort is not None and disabled_effort is not None:
        effort_reduction_pct = _pct_reduction(enabled_effort, disabled_effort)
        details["correction_effort_reduction_pct"] = effort_reduction_pct

    quality_ok = (
        quality_gain_pp is not None and quality_gain_pp >= thresholds.min_quality_improvement_pp
    )
    effort_ok = (
        effort_reduction_pct is not None
        and effort_reduction_pct >= thresholds.min_correction_effort_reduction_pct
    )
    if not (quality_ok or effort_ok):
        failures.append(
            "need either "
            f"{thresholds.min_quality_improvement_pp}pp quality improvement or "
            f"{thresholds.min_correction_effort_reduction_pct}% correction-effort reduction"
        )

    for label, enabled_val, disabled_val in (
        ("cost_usd", skills_enabled.cost_usd, skills_disabled.cost_usd),
        ("latency_ms", skills_enabled.latency_ms, skills_disabled.latency_ms),
    ):
        if enabled_val is None or disabled_val is None:
            failures.append(f"missing metric: {label}")
            continue
        increase = _pct_worsening(enabled_val, disabled_val)
        details[f"{label}_increase_pct"] = increase
        if increase > thresholds.max_cost_or_latency_increase_pct:
            failures.append(
                f"{label} increase {increase:.2f}% > {thresholds.max_cost_or_latency_increase_pct}%"
            )

    if not config.fail_closed.allow_fallback_policy_mismatch:
        if (
            skills_enabled.fallback_policy_id
            and skills_disabled.fallback_policy_id
            and skills_enabled.fallback_policy_id != skills_disabled.fallback_policy_id
        ):
            failures.append("fallback policy mismatch versus pre-registered protocol")

    return PromotionGateResult(
        passed=not failures,
        decision="promote" if not failures else "no_promote",
        failures=failures,
        details=details,
    )
