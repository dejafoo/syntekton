"""Deterministic scoring and merge with judge verdicts."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from product_factory.domain.budgets import parse_decimal
from product_factory.domain.findings import ValidatorResult
from product_factory.evaluation.cases import RUBRIC_DIMENSIONS, EvalCase
from product_factory.evaluation.judge import JudgeResult
from product_factory.evaluation.subjects import SubjectArtifact
from product_factory.tools.sandbox import run_sandboxed_command
from product_factory.validation.pipeline import (
    has_blocking_failures,
    validate_architecture_document,
    validate_architecture_request_specificity,
    validate_path_scope,
    validate_secrets,
)


class EvaluationScore(BaseModel):
    case_id: str
    subject_id: str
    seed: int = 0
    deterministic_pass: bool
    deterministic_results: list[ValidatorResult] = Field(default_factory=list)
    artifact_produced: bool = False
    patch_applies: bool | None = None
    behavioral_pass: bool | None = None
    judge_overall: int | None = None
    dimension_scores: dict[str, int] = Field(default_factory=dict)
    normalized_quality: float = 0.0
    subject_cost_usd: Decimal = Field(default=Decimal("0"))
    subject_latency_ms: int = 0
    judge_cost_usd: Decimal = Field(default=Decimal("0"))
    quality_efficiency: float = 0.0
    cost_per_usable_artifact: Decimal | None = None
    final_usable: bool = False
    judge_uncertain: bool = False
    summary: str = ""
    judge_result: JudgeResult | None = None


def run_deterministic_checks(
    case: EvalCase,
    artifact: SubjectArtifact,
    *,
    repository: Path | None = None,
    registered_commands: dict[str, dict[str, Any]] | None = None,
) -> list[ValidatorResult]:
    results: list[ValidatorResult] = list(artifact.validator_results)
    if artifact.metadata.get("live_fallback_used"):
        results.append(
            ValidatorResult(
                validator_id="live_fallback",
                status="fail",
                message="Live benchmark artifact used a deterministic fallback",
            )
        )
    if artifact.error:
        results.append(
            ValidatorResult(
                validator_id="subject_error",
                status="fail",
                message=artifact.error,
            )
        )
    artifact_text = artifact.artifact_text.strip()
    if not artifact_text:
        results.append(
            ValidatorResult(
                validator_id="artifact_empty",
                status="fail",
                message="Artifact is empty or whitespace-only",
            )
        )
        return results

    if case.workflow_type == "architecture":
        results.append(validate_architecture_document(artifact.artifact_text))
        results.extend(
            validate_architecture_request_specificity(
                artifact.artifact_text,
                must_cover=case.must_cover
                or [
                    str(item).strip()
                    for item in (case.metadata.get("must_cover") or [])
                    if str(item).strip()
                ],
                reject_boilerplate=True,
            )
        )
        results.append(validate_secrets(artifact.artifact_text))
    if case.workflow_type == "code_change":
        is_diff = artifact.artifact_kind == "patch" and (
            artifact_text.startswith("diff --git ")
            or (artifact_text.startswith("--- ") and "\n+++ " in artifact_text)
        )
        results.append(
            ValidatorResult(
                validator_id="patch_format",
                status="pass" if is_diff else "fail",
                message="Valid unified diff" if is_diff else "Artifact is not a unified diff",
            )
        )
        results.append(validate_secrets(artifact.artifact_text))
        if case.expected_files:
            missing = [f for f in case.expected_files if f not in artifact.changed_files]
            # Also accept if mentioned in patch text
            still_missing = [
                f
                for f in missing
                if f not in artifact.artifact_text and f"b/{f}" not in artifact.artifact_text
            ]
            results.append(
                ValidatorResult(
                    validator_id="expected_files",
                    status="pass" if not still_missing else "fail",
                    message="ok" if not still_missing else f"Missing: {still_missing}",
                    details={"missing": still_missing},
                )
            )
        if artifact.changed_files:
            results.append(validate_path_scope(artifact.changed_files, ["**/*"]))
        if repository is not None and artifact.artifact_kind == "patch" and artifact.artifact_text:
            from product_factory.validation.pipeline import validate_patch_applies

            patch_result = validate_patch_applies(repository, artifact.artifact_text)
            results.append(patch_result)
            if patch_result.status == "pass" and case.smoke_commands:
                results.extend(
                    _run_smoke_commands(
                        repository=repository,
                        patch=artifact.artifact_text,
                        command_ids=case.smoke_commands,
                        registered_commands=registered_commands or {},
                    )
                )
    return results


def _run_smoke_commands(
    *,
    repository: Path,
    patch: str,
    command_ids: list[str],
    registered_commands: dict[str, dict[str, Any]],
) -> list[ValidatorResult]:
    """Apply a patch to an isolated copy and execute allowlisted command IDs."""
    results: list[ValidatorResult] = []
    with tempfile.TemporaryDirectory(prefix="pf-eval-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repository, work, symlinks=True)
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=work,
            input=patch,
            capture_output=True,
            text=True,
            check=False,
        )
        if applied.returncode != 0:
            return [
                ValidatorResult(
                    validator_id="smoke_setup",
                    status="fail",
                    message="Could not apply patch in smoke-test clone",
                    details={"stderr": applied.stderr[-2000:]},
                )
            ]
        for command_id in command_ids:
            spec = registered_commands.get(command_id)
            if not spec:
                results.append(
                    ValidatorResult(
                        validator_id=f"smoke:{command_id}",
                        status="fail",
                        message=f"Unknown registered command: {command_id}",
                    )
                )
                continue
            timeout = int(spec.get("timeout_seconds", 300))
            # Sandboxed (env-scrubbed, worktree-confined) rather than raw
            # subprocess with ambient env (P1.D), matching the coordinator's
            # behavioral-validation path.
            sandbox_result = run_sandboxed_command(
                executable=str(spec["executable"]),
                args=[str(v) for v in spec.get("args", [])],
                cwd=work,
                timeout_seconds=timeout,
                pythonpath=str(work / "src"),
            )
            timed_out = sandbox_result.returncode == 124
            details = {
                "command_id": command_id,
                "exit_code": sandbox_result.returncode,
                "stdout": sandbox_result.stdout[-4000:],
                "stderr": sandbox_result.stderr[-4000:],
            }
            results.append(
                ValidatorResult(
                    validator_id=f"smoke:{command_id}",
                    status="pass" if sandbox_result.returncode == 0 else "fail",
                    message=(
                        "ok"
                        if sandbox_result.returncode == 0
                        else f"Smoke command timed out after {timeout}s"
                        if timed_out
                        else "Smoke command failed"
                    ),
                    details=details,
                )
            )
    return results


def deterministic_summary(results: list[ValidatorResult]) -> str:
    lines = [f"{r.validator_id}:{r.status}:{r.message}" for r in results]
    return "\n".join(lines) if lines else "no deterministic checks"


def merge_scores(
    *,
    case: EvalCase,
    artifact: SubjectArtifact,
    det_results: list[ValidatorResult],
    judge: JudgeResult,
    usable_threshold: int = 3,
    seed: int = 0,
) -> EvaluationScore:
    det_pass = not has_blocking_failures(det_results) and artifact.error is None
    dims = {d.name: d.score for d in judge.verdict.dimensions}
    for name in RUBRIC_DIMENSIONS:
        dims.setdefault(name, judge.verdict.overall)
    weights = case.rubric_weights
    weighted = []
    for name, score in dims.items():
        w = float(weights.get(name, 1.0))
        weighted.append(score * w)
    weight_sum = sum(float(weights.get(n, 1.0)) for n in dims) or 1.0
    mean_score = sum(weighted) / weight_sum
    if not det_pass:
        # Hard fail caps quality
        mean_score = min(mean_score, 1.0)
        overall = 1
    else:
        overall = judge.verdict.overall
    normalized = mean_score / 5.0
    subject_cost = parse_decimal(artifact.subject_cost_usd)
    judge_cost = parse_decimal(judge.usage.estimated_cost_usd)
    final_usable = det_pass and overall >= usable_threshold
    efficiency = float(Decimal("1") / subject_cost) if final_usable and subject_cost > 0 else 0.0
    patch_result = next((r for r in det_results if r.validator_id == "patch_applies"), None)
    smoke_results = [r for r in det_results if r.validator_id.startswith("smoke:")]
    return EvaluationScore(
        case_id=case.id,
        subject_id=artifact.subject_id,
        seed=seed,
        deterministic_pass=det_pass,
        deterministic_results=det_results,
        artifact_produced=bool(artifact.artifact_text.strip()),
        patch_applies=patch_result.status == "pass" if patch_result else None,
        behavioral_pass=(all(r.status == "pass" for r in smoke_results) if smoke_results else None),
        judge_overall=overall,
        dimension_scores=dims,
        normalized_quality=normalized if det_pass else min(normalized, 0.2),
        subject_cost_usd=subject_cost,
        subject_latency_ms=artifact.usage.latency_ms,
        judge_cost_usd=judge_cost,
        quality_efficiency=efficiency,
        cost_per_usable_artifact=subject_cost if final_usable else None,
        final_usable=final_usable,
        judge_uncertain=judge.verdict.uncertain,
        summary=judge.verdict.summary,
        judge_result=judge,
    )
