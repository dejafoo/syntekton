"""Unit tests for judge merge, lessons, and pairwise blinding."""

from __future__ import annotations

import random
from decimal import Decimal

from product_factory.domain.findings import ValidatorResult
from product_factory.domain.usage import UsageMetrics
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import merge_scores, run_deterministic_checks
from product_factory.evaluation.judge import MockJudge
from product_factory.evaluation.lessons import extract_lessons
from product_factory.evaluation.runners import _mock_artifact_for_case
from product_factory.evaluation.subjects import SubjectArtifact


def _case() -> EvalCase:
    return EvalCase(
        id="t1",
        workflow_type="architecture",
        request="Design a notes app",
        acceptance_criteria=["Has security section"],
    )


def _arch_artifact(text: str | None = None) -> SubjectArtifact:
    body = text or _mock_artifact_for_case(_case(), None)
    return SubjectArtifact(
        subject_id="single_agent_baseline",
        case_id="t1",
        status="completed",
        artifact_text=body,
        artifact_kind="architecture",
        usage=UsageMetrics(estimated_cost_usd=Decimal("0.05")),
    )


def test_mock_judge_scores_and_merge() -> None:
    case = _case()
    art = _arch_artifact()
    det = run_deterministic_checks(case, art)
    judge = MockJudge(base_score=4).score(case=case, artifact=art, deterministic_summary="ok")
    score = merge_scores(case=case, artifact=art, det_results=det, judge=judge)
    assert score.deterministic_pass
    assert score.final_usable
    assert score.normalized_quality > 0
    assert score.judge_overall == 4


def test_hard_fail_caps_quality() -> None:
    case = _case()
    art = SubjectArtifact(
        subject_id="single_agent_baseline",
        case_id="t1",
        status="failed",
        artifact_text="not an architecture",
        artifact_kind="architecture",
        error="boom",
    )
    det = [
        ValidatorResult(validator_id="x", status="fail", message="bad"),
    ]
    judge = MockJudge(base_score=5).score(case=case, artifact=art, deterministic_summary="FAIL")
    score = merge_scores(case=case, artifact=art, det_results=det, judge=judge)
    assert not score.deterministic_pass
    assert not score.final_usable
    assert score.judge_overall == 1


def test_pairwise_label_map() -> None:
    case = _case()
    a = _arch_artifact()
    b = a.model_copy(update={"subject_id": "full_orchestration"})
    judge = MockJudge(base_score=4)
    _, label_map = judge.pairwise(
        case=case,
        artifact_a=a,
        artifact_b=b,
        deterministic_summary="ok",
        rng=random.Random(0),
    )
    assert set(label_map.keys()) == {"a", "b"}
    assert set(label_map.values()) == {"single_agent_baseline", "full_orchestration"}


def test_lesson_extraction() -> None:
    case = _case()
    art = SubjectArtifact(
        subject_id="full_orchestration",
        case_id="t1",
        status="failed",
        artifact_kind="architecture",
        artifact_text="x",
        error="fail",
    )
    judge = MockJudge(base_score=5).score(case=case, artifact=art, deterministic_summary="FAIL")
    score = merge_scores(
        case=case,
        artifact=art,
        det_results=[ValidatorResult(validator_id="x", status="fail", message="bad")],
        judge=judge,
    )
    lessons = extract_lessons(case=case, score=score)
    assert lessons
    assert any(lesson.category == "prompt_contract" for lesson in lessons)
