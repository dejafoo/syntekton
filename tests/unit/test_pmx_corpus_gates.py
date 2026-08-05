"""PMX corpus hashes, experiment registry, and regression gates."""

from __future__ import annotations

from pathlib import Path

from product_factory.evaluation.corpus import build_corpus_snapshot
from product_factory.evaluation.experiments import (
    ExperimentRegistry,
    ScorecardMetrics,
    ScorecardRecord,
)
from product_factory.evaluation.gates import GateThresholds, evaluate_regression_gate


def test_corpus_snapshot_is_stable_and_includes_pm5_slices() -> None:
    root = Path(__file__).resolve().parents[2]
    first = build_corpus_snapshot(project_root=root, corpus_id="pmx-test")
    second = build_corpus_snapshot(project_root=root, corpus_id="pmx-test")
    assert first.content_sha256 == second.content_sha256
    assert first.case_ids
    kinds = {c.kind for c in first.components}
    assert "eval_case" in kinds
    assert "fixture" in kinds
    assert "skill" in kinds
    assert "release.readiness-review" in first.skill_versions
    assert "fhir-r4-public" in first.pack_versions or any(
        c.kind == "pack" for c in first.components
    )


def test_experiment_registry_and_regression_gate(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    corpus = build_corpus_snapshot(project_root=root, corpus_id="pmx-gate")
    registry = ExperimentRegistry(tmp_path / ".product-factory")
    experiment = registry.register_experiment(
        experiment_id="exp-pmx-1",
        corpus=corpus,
        subjects=["orchestration_with_skills", "orchestration_no_skills"],
        model_profiles=["fast_worker"],
    )
    assert experiment.corpus_sha256 == corpus.content_sha256

    passing = ScorecardRecord(
        scorecard_id="sc-pass",
        capability="release_analysis",
        skill_id="release.readiness-review",
        skill_version=corpus.skill_versions.get("release.readiness-review"),
        model_profile="fast_worker",
        subject_id="orchestration_with_skills",
        corpus_id=corpus.corpus_id,
        corpus_sha256=corpus.content_sha256,
        metrics=ScorecardMetrics(
            quality_score=0.9,
            unsupported_claim_rate=0.0,
            correct_unknown_escalation_rate=1.0,
            latency_ms=100.0,
            cost_usd=0.01,
            policy_violation_rate=0.0,
        ),
    )
    registry.record_scorecard(passing)
    ok = evaluate_regression_gate(
        experiment=experiment,
        scorecards=registry.list_scorecards(),
        thresholds=GateThresholds(
            min_quality_score=0.8,
            max_unsupported_claim_rate=0.05,
            min_correct_unknown_escalation_rate=0.9,
            max_policy_violation_rate=0.0,
        ),
    )
    assert ok.passed

    failing = passing.model_copy(
        update={
            "scorecard_id": "sc-fail",
            "metrics": ScorecardMetrics(
                quality_score=0.2,
                unsupported_claim_rate=0.4,
                policy_violation_rate=0.1,
            ),
        }
    )
    registry.record_scorecard(failing)
    blocked = evaluate_regression_gate(
        experiment=experiment,
        scorecards=registry.list_scorecards(),
        thresholds=GateThresholds(
            min_quality_score=0.8,
            max_unsupported_claim_rate=0.05,
            max_policy_violation_rate=0.0,
        ),
    )
    assert not blocked.passed
    assert any("quality_score" in f for f in blocked.failures)
