"""Case loading with backward-compatible YAML support."""

from __future__ import annotations

from pathlib import Path

import yaml

from product_factory.evaluation.cases import (
    CaseBudget,
    EvalCase,
    validate_behavioral_contract,
)


def load_eval_cases(cases_dir: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    if not cases_dir.exists():
        return cases
    for path in sorted(cases_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # Normalize simple legacy fields
        if "suite" not in data:
            data["suite"] = "local"
        if "budgets" in data and isinstance(data["budgets"], dict):
            data["budgets"] = CaseBudget.model_validate(data["budgets"])
        # Default isolation targets for coding cases if unset
        if not data.get("isolation_targets") and data.get("workflow_type") == "code_change":
            data["isolation_targets"] = ["implementation"]
        if not data.get("acceptance_criteria"):
            data["acceptance_criteria"] = [
                "Artifact is usable for the stated request",
                "Scope stays within the request",
            ]
        # Local code fixtures must have executable behavioral evidence. Keep the
        # convention centralized so legacy YAML cases become strict on load.
        if (
            data.get("workflow_type") == "code_change"
            and data.get("repository")
            and not data.get("smoke_commands")
            and not (data.get("metadata") or {}).get("behavioral_checks")
        ):
            data["smoke_commands"] = ["python_tests"]
            data.setdefault("metadata", {})["behavioral_contract_defaulted"] = True
        case = EvalCase.model_validate(data)
        validate_behavioral_contract(case)
        cases.append(case)
    return cases
