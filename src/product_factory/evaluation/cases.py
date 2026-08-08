"""Enriched evaluation case contracts."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from product_factory.domain.budgets import parse_decimal

CorpusCategory = Literal[
    "discovery",
    "technical_planning",
    "repository_change",
    "quality",
    "release",
    "operations",
]

SD6_CORPUS_CATEGORIES: tuple[CorpusCategory, ...] = (
    "discovery",
    "technical_planning",
    "repository_change",
    "quality",
    "release",
    "operations",
)

SD6_FOUNDATION_CASE_IDS: tuple[str, ...] = (
    "sd6_discovery_sparse_evidence",
    "sd6_discovery_jurisdiction_gap",
    "sd6_plan_api_boundary",
    "sd6_plan_migration_cutover",
    "sd6_repo_health_check",
    "sd6_repo_structured_logging",
    "sd6_quality_patch_review",
    "sd6_quality_evidence_gate",
    "sd6_release_missing_rollback",
    "sd6_release_ready_packet",
    "sd6_ops_incident_unknown",
    "sd6_ops_slo_breach",
)


class CaseBudget(BaseModel):
    max_cost_usd: Decimal = Field(default=Decimal("1.00"))
    max_wall_clock_seconds: int = 1800

    @field_validator("max_cost_usd", mode="before")
    @classmethod
    def _coerce_cost(cls, v: object) -> Decimal:
        return parse_decimal(v)  # type: ignore[arg-type]


class EvalCase(BaseModel):
    """Benchmark case — backward compatible with simple YAML cases."""

    id: str
    workflow_type: Literal["architecture", "code_change", "feasibility_discovery"]
    request: str
    repository: str | None = None
    tags: list[str] = Field(default_factory=list)
    expected_status: list[str] = Field(default_factory=lambda: ["completed", "awaiting_approval"])
    suite: Literal["local", "deepswe", "swe_atlas", "external", "terminal_bench"] = "local"
    corpus_category: CorpusCategory | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    rubric_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "correctness": 1.0,
            "completeness": 1.0,
            "maintainability": 1.0,
            "architectural_quality": 1.0,
            "security_awareness": 1.0,
            "test_quality": 1.0,
            "evidence_quality": 1.0,
            "scope_discipline": 1.0,
        }
    )
    isolation_targets: list[str] = Field(default_factory=list)
    reference_hints: str | None = None
    must_cover: list[str] = Field(
        default_factory=list,
        description="Request-specific topics that architecture/discovery artifacts must address",
    )
    expected_source_classes: list[str] = Field(
        default_factory=list,
        description="Source classes a feasibility dossier is expected to engage",
    )
    expected_files: list[str] = Field(default_factory=list)
    smoke_commands: list[str] = Field(default_factory=list)
    budgets: CaseBudget = Field(default_factory=CaseBudget)
    metadata: dict[str, Any] = Field(default_factory=dict)


RUBRIC_DIMENSIONS = (
    "correctness",
    "completeness",
    "maintainability",
    "architectural_quality",
    "security_awareness",
    "test_quality",
    "evidence_quality",
    "scope_discipline",
)


def validate_behavioral_contract(case: EvalCase) -> None:
    """Reject code cases that cannot produce deterministic behavioral evidence."""
    if (
        case.workflow_type == "code_change"
        and not case.smoke_commands
        and not case.metadata.get("behavioral_checks")
    ):
        raise ValueError(
            f"Code evaluation case {case.id!r} has no smoke_commands or behavioral_checks"
        )


def validate_discovery_contract(case: EvalCase) -> None:
    """Reject discovery cases that lack scoring anchors (topics or source classes)."""
    if case.workflow_type != "feasibility_discovery":
        return
    meta_classes = case.metadata.get("expected_source_classes") or []
    source_classes = case.expected_source_classes or [
        str(item).strip() for item in meta_classes if str(item).strip()
    ]
    if not case.must_cover and not source_classes:
        raise ValueError(
            f"Discovery evaluation case {case.id!r} needs must_cover or expected_source_classes"
        )


def validate_sd6_corpus_contract(case: EvalCase) -> None:
    """SD6 foundation cases must declare category coverage and sanitization notes."""
    if not case.metadata.get("sd6_corpus") and case.corpus_category is None:
        return
    if case.corpus_category is None:
        raise ValueError(f"SD6 case {case.id!r} requires corpus_category")
    if case.corpus_category not in SD6_CORPUS_CATEGORIES:
        raise ValueError(
            f"SD6 case {case.id!r} has unknown corpus_category {case.corpus_category!r}"
        )
    if not case.metadata.get("sanitization"):
        raise ValueError(f"SD6 case {case.id!r} requires metadata.sanitization")
    if not case.acceptance_criteria:
        raise ValueError(f"SD6 case {case.id!r} requires acceptance_criteria")
    if case.corpus_category in {
        "discovery",
        "technical_planning",
        "quality",
        "release",
        "operations",
    }:
        if not case.must_cover:
            raise ValueError(f"SD6 case {case.id!r} requires must_cover anchors")
