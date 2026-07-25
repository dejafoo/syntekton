"""Benchmark integrity, behavioral validation, and aggregate metric tests."""

from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from product_factory.domain.findings import ValidatorResult
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.compare import build_comparison
from product_factory.evaluation.deterministic import EvaluationScore, run_deterministic_checks
from product_factory.evaluation.loader import load_eval_cases
from product_factory.evaluation.subjects import SubjectArtifact


def _case(**updates) -> EvalCase:
    base = EvalCase(
        id="c1",
        workflow_type="code_change",
        request="Change code",
        repository="repo",
    )
    return base.model_copy(update=updates)


def _artifact(text: str, *, kind: str = "patch") -> SubjectArtifact:
    return SubjectArtifact(
        subject_id="full_orchestration",
        case_id="c1",
        status="completed",
        artifact_text=text,
        artifact_kind=kind,  # type: ignore[arg-type]
    )


def test_empty_and_non_patch_artifacts_fail() -> None:
    empty = run_deterministic_checks(_case(), _artifact(""))
    assert any(r.validator_id == "artifact_empty" and r.status == "fail" for r in empty)

    prose = run_deterministic_checks(_case(), _artifact("I changed the code."))
    assert any(r.validator_id == "patch_format" and r.status == "fail" for r in prose)


def test_local_code_cases_receive_behavioral_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    cases = load_eval_cases(root / "tests" / "eval_cases")
    code_cases = [c for c in cases if c.workflow_type == "code_change"]
    assert code_cases
    assert all(c.smoke_commands for c in code_cases)


def test_loader_rejects_code_case_without_behavioral_contract(tmp_path: Path) -> None:
    (tmp_path / "invalid.yaml").write_text(
        "id: invalid\nworkflow_type: code_change\nrequest: change code\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no smoke_commands"):
        load_eval_cases(tmp_path)


def test_smoke_command_controls_deterministic_result(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "x.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
    case = _case(smoke_commands=["check"])
    results = run_deterministic_checks(
        case,
        _artifact(patch),
        repository=repo,
        registered_commands={
            "check": {
                "executable": "python3",
                "args": [
                    "-c",
                    "import pathlib; assert 'x = 2' in pathlib.Path('x.py').read_text()",
                ],
                "timeout_seconds": 5,
            }
        },
    )
    assert any(r.validator_id == "smoke:check" and r.status == "pass" for r in results)

    unknown = run_deterministic_checks(
        case.model_copy(update={"smoke_commands": ["missing"]}),
        _artifact(patch),
        repository=repo,
        registered_commands={},
    )
    assert any(r.validator_id == "smoke:missing" and r.status == "fail" for r in unknown)

    timed_out = run_deterministic_checks(
        case.model_copy(update={"smoke_commands": ["slow"]}),
        _artifact(patch),
        repository=repo,
        registered_commands={
            "slow": {
                "executable": "python3",
                "args": ["-c", "import time; time.sleep(1)"],
                "timeout_seconds": 0,
            }
        },
    )
    assert any("timed out" in r.message for r in timed_out)


def test_comparison_aggregates_seeds_and_cost_per_usable() -> None:
    scores: list[EvaluationScore] = []
    for subject, usable in (
        ("full_orchestration", [True, False, True]),
        ("single_agent_baseline", [False, False, True]),
    ):
        for seed, ok in enumerate(usable):
            scores.append(
                EvaluationScore(
                    case_id="c1",
                    subject_id=subject,
                    seed=seed,
                    deterministic_pass=ok,
                    deterministic_results=[
                        ValidatorResult(
                            validator_id="x",
                            status="pass" if ok else "fail",
                            message="ok" if ok else "fail",
                        )
                    ],
                    artifact_produced=True,
                    patch_applies=ok,
                    behavioral_pass=ok,
                    normalized_quality=0.8 if ok else 0.2,
                    subject_cost_usd=Decimal("0.10"),
                    final_usable=ok,
                )
            )
    report = build_comparison(bench_id="b1", scores=scores)
    assert report.seeds == 3
    assert report.aggregates["full_orchestration"]["usable_rate"] == 2 / 3
    assert report.aggregates["full_orchestration"]["cost_per_usable_artifact"] == "0.15"
    assert (
        report.paired_confidence_intervals["orch_minus_single_usable_rate"]["paired_samples"] == 3
    )
