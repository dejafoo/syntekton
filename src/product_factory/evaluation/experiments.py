"""Experiment registry and scorecard contracts (PMX / SD6)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory import __version__
from product_factory.evaluation.corpus import CorpusSnapshot
from product_factory.evaluation.promotion import (
    PromotionRecord,
    RollbackRecord,
)

SCORECARD_SCHEMA_VERSION = "sd6.scorecard.v1"
HARNESS_SCHEMA_VERSION = "sd6.harness.v1"


class ScorecardMetrics(BaseModel):
    """Capability scorecard metrics from handover S6 / §10.2 / SD6 arms."""

    quality_score: float | None = None
    unsupported_claim_rate: float | None = None
    correct_unknown_escalation_rate: float | None = None
    latency_ms: float | None = None
    local_fallback_rate: float | None = None
    cost_usd: float | None = None
    policy_violation_rate: float | None = None
    structured_output_pass_rate: float | None = None
    validator_pass_rate: float | None = None
    accepted_outcome_rate: float | None = None
    human_correction_effort: float | None = None
    cloud_spend_usd: float | None = None
    category_accepted_outcome_rates: dict[str, float] = Field(default_factory=dict)


class ScorecardRecord(BaseModel):
    scorecard_id: str
    capability: str
    skill_id: str | None = None
    skill_version: str | None = None
    model_profile: str
    subject_id: str
    corpus_id: str
    corpus_sha256: str
    schema_version: str = SCORECARD_SCHEMA_VERSION
    comparison_arm: str | None = None
    harness_version: str | None = None
    seed_count: int = 1
    case_count: int = 0
    metrics: ScorecardMetrics = Field(default_factory=ScorecardMetrics)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    notes: str = ""
    evidence_level: Literal["hermetic", "integration", "operational"] = "hermetic"


class HarnessManifest(BaseModel):
    """Pinned harness identity for a scorecard / promotion batch."""

    harness_id: str = "product-factory-eval"
    schema_version: str = HARNESS_SCHEMA_VERSION
    harness_version: str = __version__
    product_factory_version: str = __version__
    corpus_id: str
    corpus_sha256: str
    scorecard_schema_version: str = SCORECARD_SCHEMA_VERSION
    promotion_config_sha256: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ExperimentManifest(BaseModel):
    """Promotion unit: corpus + subjects + policy/connector/skill digests."""

    experiment_id: str
    corpus_id: str
    corpus_sha256: str
    subjects: list[str] = Field(default_factory=list)
    pack_versions: dict[str, str] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    connector_ids: list[str] = Field(default_factory=list)
    model_profiles: list[str] = Field(default_factory=list)
    comparison_arms: list[str] = Field(default_factory=list)
    policy_digest: str | None = None
    harness_version: str | None = None
    status: Literal["draft", "active", "rejected", "promoted", "deferred"] = "draft"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentRegistry:
    """Filesystem registry under ``.product-factory/`` evaluation artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.experiments_dir = self.root / "experiments"
        self.scorecards_dir = self.root / "scorecards"
        self.harness_dir = self.root / "harness"
        self.promotions_dir = self.root / "promotions"
        self.rollbacks_dir = self.root / "rollbacks"
        for path in (
            self.experiments_dir,
            self.scorecards_dir,
            self.harness_dir,
            self.promotions_dir,
            self.rollbacks_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def register_experiment(
        self,
        *,
        experiment_id: str,
        corpus: CorpusSnapshot,
        subjects: list[str],
        model_profiles: list[str] | None = None,
        comparison_arms: list[str] | None = None,
        policy_digest: str | None = None,
        harness_version: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ExperimentManifest:
        manifest = ExperimentManifest(
            experiment_id=experiment_id,
            corpus_id=corpus.corpus_id,
            corpus_sha256=corpus.content_sha256,
            subjects=list(subjects),
            pack_versions=dict(corpus.pack_versions),
            skill_versions=dict(corpus.skill_versions),
            connector_ids=list(corpus.connector_ids),
            model_profiles=list(model_profiles or []),
            comparison_arms=list(comparison_arms or []),
            policy_digest=policy_digest,
            harness_version=harness_version or __version__,
            metadata=metadata or {},
        )
        path = self.experiments_dir / f"{experiment_id}.json"
        path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return manifest

    def get_experiment(self, experiment_id: str) -> ExperimentManifest | None:
        path = self.experiments_dir / f"{experiment_id}.json"
        if not path.is_file():
            return None
        return ExperimentManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def set_status(
        self,
        experiment_id: str,
        status: Literal["draft", "active", "rejected", "promoted", "deferred"],
    ) -> ExperimentManifest:
        manifest = self.get_experiment(experiment_id)
        if manifest is None:
            raise FileNotFoundError(experiment_id)
        updated = manifest.model_copy(update={"status": status})
        path = self.experiments_dir / f"{experiment_id}.json"
        path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return updated

    def record_scorecard(self, record: ScorecardRecord) -> Path:
        path = self.scorecards_dir / f"{record.scorecard_id}.json"
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def list_scorecards(self) -> list[ScorecardRecord]:
        records: list[ScorecardRecord] = []
        for path in sorted(self.scorecards_dir.glob("*.json")):
            records.append(ScorecardRecord.model_validate_json(path.read_text(encoding="utf-8")))
        return records

    def record_harness(self, manifest: HarnessManifest) -> Path:
        path = self.harness_dir / f"{manifest.harness_id}-{manifest.corpus_sha256[:12]}.json"
        path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def record_promotion(self, record: PromotionRecord) -> Path:
        if not record.created_at:
            record = record.model_copy(update={"created_at": datetime.now(UTC).isoformat()})
        path = self.promotions_dir / f"{record.record_id}.json"
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def record_rollback(self, record: RollbackRecord) -> Path:
        if not record.created_at:
            record = record.model_copy(update={"created_at": datetime.now(UTC).isoformat()})
        path = self.rollbacks_dir / f"{record.record_id}.json"
        path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def export_index(self) -> dict[str, Any]:
        experiments = []
        for path in sorted(self.experiments_dir.glob("*.json")):
            experiments.append(json.loads(path.read_text(encoding="utf-8")))
        promotions = []
        for path in sorted(self.promotions_dir.glob("*.json")):
            promotions.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "experiments": experiments,
            "scorecards": [r.model_dump() for r in self.list_scorecards()],
            "promotions": promotions,
            "harness": [
                json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.harness_dir.glob("*.json"))
            ],
        }


def build_deferred_promotion_record(
    *,
    experiment_id: str,
    corpus: CorpusSnapshot,
    rationale: str,
    scorecard_ids: list[str] | None = None,
    gate_failures: list[str] | None = None,
) -> PromotionRecord:
    """Explicit deferred decision when operational AMD proof is unavailable."""
    return PromotionRecord(
        record_id=f"promo-deferred-{experiment_id}",
        experiment_id=experiment_id,
        decision="deferred",
        candidate_arm="local_first_fallback",
        baseline_arm="cloud",
        corpus_id=corpus.corpus_id,
        corpus_sha256=corpus.content_sha256,
        harness_version=__version__,
        scorecard_ids=list(scorecard_ids or []),
        gate_failures=list(gate_failures or []),
        rationale=rationale,
        reviewer="hermetic",
        created_at=datetime.now(UTC).isoformat(),
        metadata={"evidence_level": "hermetic", "g4_operational_proof": False},
    )
