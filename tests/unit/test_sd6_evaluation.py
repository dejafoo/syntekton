"""SD6 evaluation corpus, promotion gates, harness, and SWE Atlas adapter."""

from __future__ import annotations

import hashlib
from pathlib import Path

from product_factory import __version__
from product_factory.evaluation.adapters.swe_atlas import (
    ExternalAdapterStore,
    SweAtlasCaseLoader,
)
from product_factory.evaluation.cases import SD6_CORPUS_CATEGORIES, SD6_FOUNDATION_CASE_IDS
from product_factory.evaluation.corpus import build_corpus_snapshot, build_sd6_corpus_catalog
from product_factory.evaluation.experiments import (
    SCORECARD_SCHEMA_VERSION,
    ExperimentRegistry,
    HarnessManifest,
    ScorecardMetrics,
    ScorecardRecord,
    build_deferred_promotion_record,
)
from product_factory.evaluation.promotion import (
    COMPARISON_ARMS,
    ArmMetrics,
    evaluate_local_first_promotion,
    evaluate_skill_promotion,
    load_sd6_promotion_config,
)

ROOT = Path(__file__).resolve().parents[2]


def test_sd6_foundation_corpus_has_twelve_cases_across_categories() -> None:
    catalog = build_sd6_corpus_catalog(project_root=ROOT)
    assert catalog.complete
    assert len(catalog.present_case_ids) == 12
    assert catalog.missing_case_ids == []
    assert set(catalog.required_case_ids) == set(SD6_FOUNDATION_CASE_IDS)
    for category in SD6_CORPUS_CATEGORIES:
        assert catalog.category_counts[category] >= 2
    for case in catalog.cases:
        assert case.metadata.get("sanitization")
        assert "sd6" in case.tags


def test_sd6_corpus_snapshot_includes_foundation_and_promotion_config() -> None:
    snap = build_corpus_snapshot(project_root=ROOT, corpus_id="sd6-foundation")
    assert len(snap.sd6_case_ids) >= 12
    assert all(cid in snap.case_ids for cid in SD6_FOUNDATION_CASE_IDS)
    kinds = {c.kind for c in snap.components}
    assert "promotion_config" in kinds
    assert snap.sd6_category_counts.get("discovery", 0) >= 2


def test_sd6_promotion_config_encodes_playbook_arms_and_thresholds() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    assert config.schema_version == "sd6.promotion.v1"
    assert set(config.arms) == set(COMPARISON_ARMS)
    assert config.min_cases_for_promotion == 30
    assert config.min_seeds_for_promotion == 3
    assert config.local_first_default.max_accepted_outcome_deficit_pp == 5.0
    assert config.local_first_default.min_cloud_spend_reduction_pct == 30.0
    assert config.skill_promotion.min_quality_improvement_pp == 5.0
    assert config.external_adapters["swe_atlas"]["status"] == "minimal"
    assert config.external_adapters["terminal_bench"]["status"] == "next"
    assert config.external_adapters["deepswe"]["status"] == "deferred"


def test_local_first_gate_defers_without_operational_proof() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    candidate = ArmMetrics(
        arm="local_first_fallback",
        policy_violation_rate=0.0,
        accepted_outcome_rate=0.9,
        human_correction_effort=1.0,
        unsupported_claim_rate=0.0,
        cloud_spend_usd=1.0,
        latency_tradeoff_documented=True,
        case_count=30,
        seed_count=3,
    )
    cloud = ArmMetrics(
        arm="cloud",
        accepted_outcome_rate=0.92,
        human_correction_effort=1.0,
        unsupported_claim_rate=0.0,
        cloud_spend_usd=2.0,
    )
    result = evaluate_local_first_promotion(
        candidate=candidate, cloud=cloud, config=config, operational_ready=False
    )
    assert not result.passed
    assert result.decision == "deferred"


def test_local_first_gate_enforces_playbook_thresholds() -> None:
    config = load_sd6_promotion_config(ROOT / "config" / "evaluation" / "sd6_promotion.yaml")
    cloud = ArmMetrics(
        arm="cloud",
        policy_violation_rate=0.0,
        accepted_outcome_rate=0.90,
        human_correction_effort=1.0,
        unsupported_claim_rate=0.02,
        cloud_spend_usd=10.0,
        category_accepted_outcome_rates={"discovery": 0.9, "release": 0.9},
    )
    passing = ArmMetrics(
        arm="local_first_fallback",
        policy_violation_rate=0.0,
        accepted_outcome_rate=0.88,  # 2pp below cloud
        human_correction_effort=1.05,  # 5% worse
        unsupported_claim_rate=0.03,  # 1pp worse
        cloud_spend_usd=6.0,  # 40% lower
        latency_tradeoff_documented=True,
        unresolved_reliability_regression=False,
        case_count=30,
        seed_count=3,
        category_accepted_outcome_rates={"discovery": 0.88, "release": 0.87},
    )
    ok = evaluate_local_first_promotion(
        candidate=passing, cloud=cloud, config=config, operational_ready=True
    )
    assert ok.passed
    assert ok.decision == "promote"

    failing = passing.model_copy(
        update={
            "accepted_outcome_rate": 0.80,  # 10pp below
            "cloud_spend_usd": 9.0,  # only 10% lower
            "policy_violation_rate": 0.1,
        }
    )
    blocked = evaluate_local_first_promotion(
        candidate=failing, cloud=cloud, config=config, operational_ready=True
    )
    assert not blocked.passed
    assert blocked.decision == "no_promote"
    assert any("policy_violation_rate" in f for f in blocked.failures)
    assert any("accepted_outcome" in f for f in blocked.failures)
    assert any("cloud spend" in f for f in blocked.failures)


def test_skill_promotion_requires_quality_or_effort_gain() -> None:
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
        quality_score=0.78,  # +8pp
        human_correction_effort=1.9,
        cost_usd=1.1,  # +10%
        latency_ms=1100.0,
        fallback_policy_id="skills-ablation-v1",
    )
    ok = evaluate_skill_promotion(
        skills_enabled=enabled,
        skills_disabled=disabled,
        config=config,
        operational_ready=True,
    )
    assert ok.passed

    flat = enabled.model_copy(update={"quality_score": 0.71, "human_correction_effort": 2.0})
    blocked = evaluate_skill_promotion(
        skills_enabled=flat,
        skills_disabled=disabled,
        config=config,
        operational_ready=True,
    )
    assert not blocked.passed
    assert any("quality improvement" in f or "correction-effort" in f for f in blocked.failures)


def test_harness_scorecard_and_deferred_promotion_records(tmp_path: Path) -> None:
    corpus = build_corpus_snapshot(project_root=ROOT, corpus_id="sd6-foundation")
    config_path = ROOT / "config" / "evaluation" / "sd6_promotion.yaml"
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    registry = ExperimentRegistry(tmp_path / ".product-factory")
    experiment = registry.register_experiment(
        experiment_id="exp-sd6-hermetic",
        corpus=corpus,
        subjects=["orchestration_with_skills", "orchestration_no_skills", "single_agent_baseline"],
        comparison_arms=list(COMPARISON_ARMS),
        model_profiles=["coding_worker"],
        metadata={"evidence_level": "hermetic"},
    )
    harness = HarnessManifest(
        corpus_id=corpus.corpus_id,
        corpus_sha256=corpus.content_sha256,
        promotion_config_sha256=config_sha,
        configuration={"seeds": 1, "live": False, "arms": list(COMPARISON_ARMS)},
    )
    harness_path = registry.record_harness(harness)
    assert harness_path.is_file()

    scorecards: list[str] = []
    for arm in ("local_first_fallback", "cloud", "skills_enabled", "skills_disabled"):
        record = ScorecardRecord(
            scorecard_id=f"sc-hermetic-{arm}",
            capability="sd6_foundation",
            model_profile="coding_worker",
            subject_id=(
                "orchestration_with_skills"
                if arm == "skills_enabled"
                else "orchestration_no_skills"
                if arm == "skills_disabled"
                else "full_orchestration"
            ),
            corpus_id=corpus.corpus_id,
            corpus_sha256=corpus.content_sha256,
            comparison_arm=arm,
            harness_version=__version__,
            seed_count=1,
            case_count=len(SD6_FOUNDATION_CASE_IDS),
            evidence_level="hermetic",
            metrics=ScorecardMetrics(
                quality_score=0.5,
                policy_violation_rate=0.0,
                validator_pass_rate=1.0,
                accepted_outcome_rate=0.5,
                unsupported_claim_rate=0.0,
            ),
            notes="Hermetic fixture scorecard; not an AMD operational run.",
        )
        registry.record_scorecard(record)
        scorecards.append(record.scorecard_id)

    deferred = build_deferred_promotion_record(
        experiment_id=experiment.experiment_id,
        corpus=corpus,
        rationale=(
            "G4 operational proof deferred: no AMD-owned 30×3-seed promotion run "
            "in this environment. Hermetic SD6 foundation is complete."
        ),
        scorecard_ids=scorecards,
        gate_failures=["operational AMD-owned multi-seed promotion run not available"],
    )
    path = registry.record_promotion(deferred)
    assert path.is_file()
    assert deferred.decision == "deferred"
    loaded = registry.list_scorecards()
    assert len(loaded) == 4
    assert all(s.schema_version == SCORECARD_SCHEMA_VERSION for s in loaded)
    registry.set_status(experiment.experiment_id, "deferred")
    assert registry.get_experiment(experiment.experiment_id).status == "deferred"


def test_swe_atlas_adapter_writes_durable_mapping(tmp_path: Path) -> None:
    loader = SweAtlasCaseLoader(
        records=[
            {
                "id": "atlas-demo-1",
                "task": "Inspect repository structure and propose a focused refactor.",
                "task_class": "refactoring",
                "acceptance_criteria": ["Refactor stays scoped", "Tests remain green"],
                "native_metrics": ["task_success", "edit_distance"],
            }
        ],
        atlas_version="fixture-0.0.1",
        adapter_version="0.1.0",
    )
    cases = loader.load()
    assert len(cases) == 1
    assert cases[0].suite == "swe_atlas"
    assert cases[0].metadata["atlas_case_id"] == "atlas-demo-1"
    record = loader.build_mapping_record()
    assert record.content_sha256
    assert record.live_run is False
    store = ExternalAdapterStore(tmp_path / ".product-factory")
    path = store.save_swe_atlas(record)
    assert path.is_file()
    listed = store.list_swe_atlas()
    assert len(listed) == 1
    assert listed[0].case_mappings[0].eval_case_id == cases[0].id
