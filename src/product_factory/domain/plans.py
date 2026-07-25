"""Plan contracts and planner output schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec


class Assumption(BaseModel):
    id: str
    description: str
    verification_required: bool = True


class FinalArtifactSpec(BaseModel):
    logical_name: str
    composer_task_id: str
    # Stable deliverable role from the workflow pack's land map. When set, the
    # planner may also propose a request-specific name and destination.
    role: str | None = None
    dest_path: str | None = None


class PlannerOutput(BaseModel):
    """Strict structured output from the planner model."""

    model_config = {"extra": "forbid"}

    objective: str
    assumptions: list[Assumption] = Field(default_factory=list)
    tasks: list[TaskSpec]
    final_artifacts: list[FinalArtifactSpec] = Field(default_factory=list)
    validation_strategy: str = ""
    risk_classification: Literal["low", "medium", "high"] = "low"
    request_acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)


class CompilerError(BaseModel):
    code: str
    message: str
    task_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CompiledPlan(BaseModel):
    objective: str
    assumptions: list[Assumption]
    tasks: dict[str, TaskSpec]
    task_order: list[str]
    final_artifacts: list[FinalArtifactSpec]
    validation_strategy: str
    risk_classification: Literal["low", "medium", "high"]
    request_acceptance_criteria: list[AcceptanceCriterion]
    compiler_notes: list[str] = Field(default_factory=list)


class CompileResult(BaseModel):
    ok: bool
    plan: CompiledPlan | None = None
    errors: list[CompilerError] = Field(default_factory=list)
