"""Immutable inputs and outcomes for deliverable composition."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from product_factory.domain.findings import Finding
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskResult, TaskSpec
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway


@dataclass(frozen=True, slots=True)
class CompositionInput:
    """Data-only boundary supplied to a registered document composer."""

    request: RunRequest
    role: str
    document_name: str
    run_id: str = ""
    profile: str = ""
    base_revision: str = ""
    use_mock: bool = True
    task: TaskSpec | None = None
    gateway: ModelGateway | None = None
    context_messages: tuple[dict[str, Any], ...] = ()
    findings: tuple[Finding, ...] = ()
    task_results: tuple[TaskResult, ...] = ()
    dependency_outputs: tuple[dict[str, Any], ...] = ()
    validation_evidence_refs: tuple[str, ...] = ()
    validator_results: tuple[dict[str, Any], ...] = ()
    lineage: tuple[dict[str, str], ...] = ()
    effective_policy: dict[str, Any] | None = None
    generated_document: str | None = None
    generated_usage: UsageMetrics = field(default_factory=UsageMetrics)

    @property
    def pack_input(self) -> dict[str, Any]:
        return self.request.pack_input or {}


@dataclass(frozen=True, slots=True)
class CompositionOutcome:
    """Typed document result; absence is never represented as success."""

    role: str
    media_type: str
    body: str
    usage: UsageMetrics = field(default_factory=UsageMetrics)
    evidence_refs: tuple[str, ...] = ()
    complete: bool = True
