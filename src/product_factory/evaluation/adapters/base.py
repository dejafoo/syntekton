"""Extensibility adapters for future public evaluation suites."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.subjects import SubjectArtifact, SubjectConfig
from product_factory.gateway.base import ModelGateway


@runtime_checkable
class CaseLoader(Protocol):
    """Load benchmark cases from a suite-specific source."""

    suite_name: str

    def load(self, *, limit: int | None = None) -> list[EvalCase]: ...


@runtime_checkable
class SubjectRunner(Protocol):
    """Run a subject against one evaluation case."""

    subject_id: str

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact: ...


class LocalYamlCaseLoader:
    """Default loader for tests/eval_cases YAML files."""

    suite_name = "local"

    def __init__(self, cases_dir: Path) -> None:
        self.cases_dir = cases_dir

    def load(self, *, limit: int | None = None) -> list[EvalCase]:
        from product_factory.evaluation.loader import load_eval_cases

        cases = load_eval_cases(self.cases_dir)
        if limit is not None:
            return cases[:limit]
        return cases


class ExternalSuiteCaseLoader:
    """Stub adapter proving foreign cases can map into EvalCase.

    Public suites (DeepSWE, SWE Atlas) should implement CaseLoader by converting
    their native records into EvalCase without changing the judge.
    """

    suite_name = "external"

    def __init__(self, records: list[dict] | None = None) -> None:
        self.records = records or []

    def load(self, *, limit: int | None = None) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for raw in self.records:
            cases.append(
                EvalCase(
                    id=str(raw.get("id", "external-unknown")),
                    workflow_type=raw.get("workflow_type", "code_change"),  # type: ignore[arg-type]
                    request=str(raw.get("prompt") or raw.get("request") or ""),
                    repository=raw.get("repository"),
                    tags=list(raw.get("tags") or ["external"]),
                    suite="external",
                    acceptance_criteria=list(raw.get("acceptance_criteria") or []),
                    reference_hints=raw.get("reference_hints"),
                    isolation_targets=list(raw.get("isolation_targets") or []),
                )
            )
        if limit is not None:
            return cases[:limit]
        return cases
