"""Tests for human-gated lesson loop."""

from __future__ import annotations

from pathlib import Path

from product_factory.domain.findings import ValidatorResult
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import EvaluationScore
from product_factory.evaluation.lessons import (
    bump_skill_version,
    derive_theme,
    extract_lessons,
    is_actionable_subject,
    list_lessons,
    promote_lessons,
    reject_lessons_matching,
    summarize_lessons,
    update_lesson_status,
    write_lesson_candidates,
)


def test_actionable_subject_prefixes() -> None:
    assert is_actionable_subject("full_orchestration")
    assert is_actionable_subject("full_orchestration_with_review")
    assert is_actionable_subject("orchestration_live_planner")
    assert is_actionable_subject("seeded_repair")
    assert not is_actionable_subject("single_agent_baseline")
    assert not is_actionable_subject("frontier_reference")


def test_derive_theme_from_validators_and_dims() -> None:
    assert (
        derive_theme(
            deterministic_results=[
                ValidatorResult(
                    validator_id="architecture_must_cover",
                    status="fail",
                    message="Missing must-cover topics: ['x']",
                )
            ]
        )
        == "must_cover"
    )
    assert (
        derive_theme(
            deterministic_results=[
                ValidatorResult(
                    validator_id="architecture_sections",
                    status="fail",
                    message="Architecture document incomplete",
                )
            ]
        )
        == "arch_incomplete"
    )
    assert derive_theme(weak_dimensions=["evidence_quality"]) == "weak_evidence"


def test_extract_lessons_sets_theme_and_actionable() -> None:
    case = EvalCase(
        id="code_cache",
        title="t",
        request="r",
        workflow_type="code_change",
        acceptance_criteria=["a"],
    )
    score = EvaluationScore(
        case_id=case.id,
        subject_id="full_orchestration",
        deterministic_pass=False,
        final_usable=False,
        deterministic_results=[
            ValidatorResult(
                validator_id="architecture_sections",
                status="fail",
                message="Architecture document incomplete",
            )
        ],
    )
    lessons = extract_lessons(case=case, score=score, source_bench_id="bench-test")
    assert lessons
    assert all(lesson.actionable for lesson in lessons)
    assert any(lesson.theme == "arch_incomplete" for lesson in lessons)
    assert all(lesson.source_bench_id == "bench-test" for lesson in lessons)


def test_status_transitions_and_orch_filter(tmp_path: Path) -> None:
    pf = tmp_path / ".product-factory"
    case = EvalCase(
        id="arch_saas",
        title="t",
        request="r",
        workflow_type="architecture",
        acceptance_criteria=["a"],
    )
    orch = EvaluationScore(
        case_id="arch_saas",
        subject_id="full_orchestration",
        deterministic_pass=False,
        final_usable=False,
        deterministic_results=[
            ValidatorResult(
                validator_id="architecture_sections",
                status="fail",
                message="Architecture document incomplete",
            )
        ],
    )
    base = EvaluationScore(
        case_id="arch_saas",
        subject_id="single_agent_baseline",
        deterministic_pass=False,
        final_usable=False,
        deterministic_results=[
            ValidatorResult(
                validator_id="architecture_sections",
                status="fail",
                message="Architecture document incomplete",
            )
        ],
    )
    lessons = extract_lessons(case=case, score=orch, source_bench_id="bench-x") + extract_lessons(
        case=case, score=base, source_bench_id="bench-x"
    )
    write_lesson_candidates(lessons, pf / "lessons" / "candidates" / "bench-x")
    orch_only = list_lessons(pf, bench_id="bench-x", orch_only=True)
    assert len(orch_only) == 1
    assert orch_only[0].subject_id == "full_orchestration"
    all_lessons = list_lessons(pf, bench_id="bench-x", orch_only=False)
    assert len(all_lessons) == 2

    accepted = update_lesson_status(pf, orch_only[0].id, status="accepted", note="keep")
    assert accepted.status == "accepted"
    assert (pf / "lessons" / "candidates" / "bench-x" / "decisions.jsonl").exists()

    rejected = reject_lessons_matching(pf, bench_id="bench-x", filter_name="baseline")
    assert len(rejected) == 1
    assert rejected[0].status == "rejected"

    summary = summarize_lessons(list_lessons(pf, bench_id="bench-x", orch_only=False))
    assert summary["count"] == 2
    assert summary["by_status"]["accepted"] == 1
    assert summary["by_status"]["rejected"] == 1


def test_promote_bumps_skill_version(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    skill_dir = project / "skills" / "architecture" / "system-design"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# System Design\nBe specific.\n", encoding="utf-8")
    (skill_dir / "manifest.yaml").write_text(
        "id: architecture.system-design\nversion: 1.0.0\n"
        "title: System Design\ncapabilities: [architecture]\n"
        "content_ref: SKILL.md\nstatus: active\n",
        encoding="utf-8",
    )
    pf = project / ".product-factory"
    case = EvalCase(
        id="arch_saas",
        title="t",
        request="r",
        workflow_type="architecture",
        acceptance_criteria=["a"],
    )
    score = EvaluationScore(
        case_id="arch_saas",
        subject_id="full_orchestration",
        deterministic_pass=False,
        final_usable=False,
        deterministic_results=[
            ValidatorResult(
                validator_id="architecture_must_cover",
                status="fail",
                message="Missing must-cover topics: ['tenant']",
            )
        ],
    )
    lessons = extract_lessons(case=case, score=score, source_bench_id="bench-y")
    write_lesson_candidates(lessons, pf / "lessons" / "candidates" / "bench-y")
    lesson_id = lessons[0].id
    update_lesson_status(pf, lesson_id, status="accepted", bench_id="bench-y")

    sid, ver = bump_skill_version(skill_dir)
    assert sid == "architecture.system-design"
    assert ver == "1.0.1"
    # reset for promote which bumps again
    (skill_dir / "manifest.yaml").write_text(
        "id: architecture.system-design\nversion: 1.0.0\n"
        "title: System Design\ncapabilities: [architecture]\n"
        "content_ref: SKILL.md\nstatus: active\n",
        encoding="utf-8",
    )

    ledger = promote_lessons(
        pf,
        lesson_ids=[lesson_id],
        files=[skill_dir / "SKILL.md"],
        bump_skill_ids=["architecture.system-design"],
        project_root=project,
        note="test promote",
        bench_id="bench-y",
    )
    assert ledger["skill_versions"]["architecture.system-design"] == "1.0.1"
    promoted = list_lessons(pf, bench_id="bench-y", orch_only=True, status="promoted")
    assert len(promoted) == 1
    assert promoted[0].promoted_refs
    assert Path(ledger["ledger_path"]).exists()
