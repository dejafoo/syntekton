"""Subject configuration and artifact bundles."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.findings import ValidatorResult
from product_factory.domain.usage import UsageMetrics

SubjectId = Literal[
    "full_orchestration",
    "single_agent_baseline",
    "agent_isolation",
    "implementation_isolation",
    "orchestration_validation_repair",
    "full_orchestration_no_review",
    "full_orchestration_with_review",
    "orchestration_file_list_context",
    "orchestration_targeted_context",
    "orchestration_fixed_planner",
    "orchestration_live_planner",
    "orchestration_complexity_planner",
    "frontier_reference",
]


class SubjectConfig(BaseModel):
    subject_id: SubjectId
    model_profile: str = "supervisor"
    allow_one_repair: bool = True
    isolation_capability: str | None = None
    description: str = ""


class SubjectArtifact(BaseModel):
    """Outputs produced by a subject for a single case."""

    subject_id: SubjectId
    case_id: str
    status: str
    artifact_text: str = ""
    artifact_kind: Literal["patch", "architecture", "json", "other"] = "other"
    artifact_path: Path | None = None
    changed_files: list[str] = Field(default_factory=list)
    run_id: str | None = None
    prompt_package_hash: str = ""
    skill_versions: dict[str, str] = Field(default_factory=dict)
    model_profile: str = ""
    resolved_model_id: str = ""
    provider: str = ""
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    validator_results: list[ValidatorResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    @property
    def subject_cost_usd(self) -> Decimal:
        return self.usage.estimated_cost_usd
