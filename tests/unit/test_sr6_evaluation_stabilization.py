"""SR6.A hermetic stabilization corpus and evidence-level promotion guardrails."""

from __future__ import annotations

from pathlib import Path

from product_factory.evaluation.cases import (
    SD6_CORPUS_CATEGORIES,
    SD6_FOUNDATION_CASE_IDS,
    SR6_STABILIZATION_CASE_IDS,
    SR6_STABILIZATION_CORPUS_ID,
    SR6_STABILIZATION_SEED_COUNT,
)
from product_factory.evaluation.corpus import build_sr6_stabilization_catalog
from product_factory.evaluation.experiments import ScorecardMetrics, ScorecardRecord
from product_factory.evaluation.promotion import (
    ArmMetrics,
    evaluate_local_first_promotion,
    evaluate_skill_promotion,
    evidence_allows_promotion,
    label_evidence_level,
    load_sd6_promotion_config,
)

ROOT = Path(__file__).resolve().parents[2]


def _passing_local_first_pair() -> tuple[ArmMetrics, ArmMetrics]:
    cloud = ArmMetrics(
        arm="cloud",
        policy_violation_rate=0.0,
        accepted_outcome_rate=0.90,
        human_correction_effort=1.0,
        unsupported_claim_rate=0.02,
        cloud_spend_usd=10.0,
        category_accepted_outcome_rates={"discovery": 0.9, "release": 0.9},
    )
    candidate = ArmMetrics(
        arm="local_first_fallback",
        policy_violation_rate=0.0,
        accepted_outcome_rate=0.88,
        human_correction_effort=1.05,
        unsupported_claim_rate=0.03,
        cloud_spend_usd=6.0,
        latency_tradeoff_documented=True,
        unresolved_reliability_regression=False,
        case_count=30,
        seed_count=3,
        category_accepted_outcome_rates={"discovery": 0.88, "release": 0.87},
    )
    return candidate, cloud


def test_sr6_stabilization_catalog_reuses_twelve_case_foundation() -> None:
    catalog = build_sr6_stabilization_catalog(project_root=ROOT)
    assert catalog.corpus_id == SR6_STABILIZATION_CORPUS_ID
    assert catalog.complete
    assert catalog.seed_count == SR6_STABILIZATION_SEED_COUNT == 1
    assert catalog.evidence_level == "hermetic"
    assert catalog.may_promote is False
    assert "AMD" in catalog.claim
    assert set(catalog.required_case_ids) == set(SR6_STABILIZATION_CASE_IDS)
    assert set(SR6_STABILIZATION_CASE_IDS) == set(SD6_FOUNDATION_CASE_IDS)
    assert len(catalog.present_case_ids) == 12
    for category in SD6_CORPUS_CATEGORIES:
        assert catalog.category_counts[category] >= 2


def test_sr6_promotion_config_marks_stabilization_non_promotable() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    assert config.stabilization.corpus_id == SR6_STABILIZATION_CORPUS_ID
    assert config.stabilization.case_count == 12
    assert config.stabilization.seed_count == 1
    assert config.stabilization.may_promote is False
    assert config.stabilization.evidence_level == "hermetic"
    assert config.fail_closed.allow_hermetic_or_mock_promotion is False


def test_label_evidence_level_downgrades_false_operational_claims() -> None:
    assert label_evidence_level(is_mock=True, evidence_level="operational", live_amd=True) == "mock"
    assert label_evidence_level(evidence_level="operational", live_amd=False) == "hermetic"
    assert label_evidence_level(evidence_level="hermetic") == "hermetic"
    assert label_evidence_level(evidence_level="operational", live_amd=True) == "operational"


def test_evidence_allows_promotion_fail_closed_for_non_operational() -> None:
    for level in ("mock", "hermetic", "integration"):
        allowed, reason = evidence_allows_promotion(level)
        assert allowed is False
        assert reason is not None
        assert level in reason or "mock" in reason

    mock_blocked, mock_reason = evidence_allows_promotion("operational", is_mock=True)
    assert mock_blocked is False
    assert mock_reason is not None
    assert "mock" in mock_reason

    ok, ok_reason = evidence_allows_promotion("operational", is_mock=False)
    assert ok is True
    assert ok_reason is None


def test_local_first_gate_rejects_hermetic_and_mock_even_when_metrics_pass() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    candidate, cloud = _passing_local_first_pair()

    hermetic = evaluate_local_first_promotion(
        candidate=candidate,
        cloud=cloud,
        config=config,
        operational_ready=True,
        evidence_level="hermetic",
    )
    assert hermetic.passed is False
    assert hermetic.decision == "deferred"
    assert any("hermetic" in f for f in hermetic.failures)

    mocked = evaluate_local_first_promotion(
        candidate=candidate,
        cloud=cloud,
        config=config,
        operational_ready=True,
        evidence_level="operational",
        is_mock=True,
    )
    assert mocked.passed is False
    assert mocked.decision == "deferred"
    assert any("mock" in f for f in mocked.failures)

    integration = evaluate_local_first_promotion(
        candidate=candidate,
        cloud=cloud,
        config=config,
        operational_ready=True,
        evidence_level="integration",
    )
    assert integration.passed is False
    assert integration.decision == "deferred"


def test_skill_gate_rejects_hermetic_scorecards() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    disabled = ArmMetrics(
        arm="skills_disabled",
        policy_violation_rate=0.0,
        quality_score=0.70,
        human_correction_effort=2.0,
        cost_usd=1.0,
        latency_ms=1000.0,
        fallback_policy_id="skills-ablation-v1",
    )
    enabled = ArmMetrics(
        arm="skills_enabled",
        policy_violation_rate=0.0,
        quality_score=0.78,
        human_correction_effort=1.9,
        cost_usd=1.1,
        latency_ms=1100.0,
        fallback_policy_id="skills-ablation-v1",
    )
    result = evaluate_skill_promotion(
        skills_enabled=enabled,
        skills_disabled=disabled,
        config=config,
        operational_ready=True,
        evidence_level="hermetic",
    )
    assert result.passed is False
    assert result.decision == "deferred"
    assert any("hermetic" in f for f in result.failures)


def test_hermetic_scorecard_record_is_labeled_non_operational() -> None:
    record = ScorecardRecord(
        scorecard_id="sc-sr6-stabilization-local",
        capability="sr6_stabilization",
        model_profile="mock",
        subject_id="full_orchestration",
        corpus_id=SR6_STABILIZATION_CORPUS_ID,
        corpus_sha256="abc",
        comparison_arm="local_first_fallback",
        seed_count=SR6_STABILIZATION_SEED_COUNT,
        case_count=12,
        evidence_level="hermetic",
        metrics=ScorecardMetrics(quality_score=0.9, policy_violation_rate=0.0),
        notes="Stabilization fixture; not AMD proof.",
    )
    assert record.evidence_level == "hermetic"
    allowed, _ = evidence_allows_promotion(record.evidence_level)
    assert allowed is False
