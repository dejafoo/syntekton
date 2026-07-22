"""Domain package exports and JSON Schema helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory.domain.artifacts import ArtifactRef, ResourceRef
from product_factory.domain.budgets import RunBudget, TaskBudget
from product_factory.domain.errors import (
    ApprovalBlockedError,
    BudgetExhaustedError,
    ConfigurationError,
    PlanRejectedError,
    ProductFactoryError,
    ProviderError,
    RuntimeFailureError,
    SchemaValidationError,
    ToolAuthorizationError,
    UnsafeOperationError,
    ValidationFailureError,
)
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.plans import (
    Assumption,
    CompiledPlan,
    CompilerError,
    CompileResult,
    FinalArtifactSpec,
    PlannerOutput,
)
from product_factory.domain.runs import FinalStatus, RunManifest, RunRequest, WorkflowType
from product_factory.domain.tasks import AcceptanceCriterion, TaskResult, TaskSpec
from product_factory.domain.tools import CapabilityGrant, ToolCallRecord, ToolDefinition
from product_factory.domain.usage import UsageMetrics

SCHEMA_MODELS: dict[str, type] = {
    "RunRequest": RunRequest,
    "RunBudget": RunBudget,
    "TaskSpec": TaskSpec,
    "TaskBudget": TaskBudget,
    "AcceptanceCriterion": AcceptanceCriterion,
    "ResourceRef": ResourceRef,
    "ArtifactRef": ArtifactRef,
    "Finding": Finding,
    "TaskResult": TaskResult,
    "UsageMetrics": UsageMetrics,
    "PlannerOutput": PlannerOutput,
    "CompiledPlan": CompiledPlan,
    "ValidatorResult": ValidatorResult,
}


def model_json_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_MODELS:
        raise KeyError(f"Unknown schema model: {name}")
    return SCHEMA_MODELS[name].model_json_schema()


def export_json_schemas(output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, model in SCHEMA_MODELS.items():
        path = output_dir / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8")
        written.append(path)
    return written


__all__ = [
    "AcceptanceCriterion",
    "ApprovalBlockedError",
    "ArtifactRef",
    "Assumption",
    "BudgetExhaustedError",
    "CapabilityGrant",
    "CompiledPlan",
    "CompileResult",
    "CompilerError",
    "ConfigurationError",
    "FinalArtifactSpec",
    "FinalStatus",
    "Finding",
    "PlanRejectedError",
    "PlannerOutput",
    "ProductFactoryError",
    "ProviderError",
    "ResourceRef",
    "RunBudget",
    "RunManifest",
    "RunRequest",
    "RuntimeFailureError",
    "SCHEMA_MODELS",
    "SchemaValidationError",
    "TaskBudget",
    "TaskResult",
    "TaskSpec",
    "ToolAuthorizationError",
    "ToolCallRecord",
    "ToolDefinition",
    "UnsafeOperationError",
    "UsageMetrics",
    "ValidationFailureError",
    "ValidatorResult",
    "WorkflowType",
    "export_json_schemas",
    "model_json_schema",
]
