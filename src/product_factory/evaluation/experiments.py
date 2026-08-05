"""Experiment registry and scorecard contracts (PMX)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.evaluation.corpus import CorpusSnapshot


class ScorecardMetrics(BaseModel):
    """Capability scorecard metrics from handover S6 / §10.2."""

    quality_score: float | None = None
    unsupported_claim_rate: float | None = None
    correct_unknown_escalation_rate: float | None = None
    latency_ms: float | None = None
    local_fallback_rate: float | None = None
    cost_usd: float | None = None
    policy_violation_rate: float | None = None
    structured_output_pass_rate: float | None = None
    validator_pass_rate: float | None = None


class ScorecardRecord(BaseModel):
    scorecard_id: str
    capability: str
    skill_id: str | None = None
    skill_version: str | None = None
    model_profile: str
    subject_id: str
    corpus_id: str
    corpus_sha256: str
    metrics: ScorecardMetrics = Field(default_factory=ScorecardMetrics)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    notes: str = ""


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
    policy_digest: str | None = None
    status: Literal["draft", "active", "rejected", "promoted"] = "draft"
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExperimentRegistry:
    """Filesystem registry under ``.product-factory/experiments/``."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.experiments_dir = self.root / "experiments"
        self.scorecards_dir = self.root / "scorecards"
        self.experiments_dir.mkdir(parents=True, exist_ok=True)
        self.scorecards_dir.mkdir(parents=True, exist_ok=True)

    def register_experiment(
        self,
        *,
        experiment_id: str,
        corpus: CorpusSnapshot,
        subjects: list[str],
        model_profiles: list[str] | None = None,
        policy_digest: str | None = None,
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
            policy_digest=policy_digest,
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
        status: Literal["draft", "active", "rejected", "promoted"],
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

    def export_index(self) -> dict[str, Any]:
        experiments = []
        for path in sorted(self.experiments_dir.glob("*.json")):
            experiments.append(json.loads(path.read_text(encoding="utf-8")))
        return {
            "experiments": experiments,
            "scorecards": [r.model_dump() for r in self.list_scorecards()],
        }
