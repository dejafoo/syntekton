"""Unit tests for review evidence parsing and demotion."""

from __future__ import annotations

from product_factory.domain.artifacts import ResourceRef
from product_factory.orchestration.review_findings import (
    apply_evidence_demotion,
    evidence_path_resolves,
    parse_raw_findings,
    score_seeded_review_detection,
    validate_review_findings,
)


def _patch_ref() -> ResourceRef:
    return ResourceRef(
        id="patch-1",
        resource_type="patch",
        origin="task",
        scope="patch",
        trust_level="mixed",
    )


def test_evidence_path_in_patch() -> None:
    patch = "diff --git a/src/app/cache.py b/src/app/cache.py\n+++ b/src/app/cache.py\n"
    assert evidence_path_resolves("src/app/cache.py", review_patch=patch, worktree_root=None)
    assert not evidence_path_resolves("missing.py", review_patch=patch, worktree_root=None)


def test_blocking_without_evidence_demoted() -> None:
    sev, conf = apply_evidence_demotion(severity="blocking", confidence=0.95, evidence_ok=False)
    assert sev == "major"
    assert conf <= 0.49


def test_blocking_low_confidence_demoted() -> None:
    sev, conf = apply_evidence_demotion(severity="blocking", confidence=0.5, evidence_ok=True)
    assert sev == "major"
    assert conf == 0.5


def test_parse_raw_findings_demotes_bad_evidence() -> None:
    findings = parse_raw_findings(
        [
            {
                "category": "correctness",
                "severity": "blocking",
                "summary": "Bug",
                "explanation": "x",
                "recommended_action": "fix",
                "confidence": 0.9,
                "evidence_path": "does-not-exist.py",
            }
        ],
        task_id="T1",
        produced_by="reviewer",
        patch_ref=_patch_ref(),
        review_patch="+++ b/src/app/cache.py\n",
        worktree_root=None,
    )
    assert findings[0].severity == "major"
    assert findings[0].confidence <= 0.49


def test_validate_review_findings_requires_path_scope() -> None:
    findings = parse_raw_findings(
        [
            {
                "category": "correctness",
                "severity": "blocking",
                "summary": "Bug in cache",
                "explanation": "returns None",
                "recommended_action": "fix get",
                "confidence": 0.95,
                "evidence_path": "src/app/cache.py",
            }
        ],
        task_id="T1",
        produced_by="reviewer",
        patch_ref=_patch_ref(),
        review_patch="+++ b/src/app/cache.py\n",
        worktree_root=None,
    )
    assert findings[0].severity == "blocking"
    assert validate_review_findings(findings).status == "pass"


def test_score_seeded_review_detection() -> None:
    findings = parse_raw_findings(
        [
            {
                "category": "correctness",
                "severity": "blocking",
                "summary": "Broken get in src/app/cache.py",
                "explanation": "always None",
                "recommended_action": "fix",
                "confidence": 0.95,
                "evidence_path": "src/app/cache.py",
            }
        ],
        task_id="T1",
        produced_by="reviewer",
        patch_ref=_patch_ref(),
        review_patch="+++ b/src/app/cache.py\n",
        worktree_root=None,
    )
    ok = score_seeded_review_detection(
        findings, seeded_paths=["src/app/cache.py"], expect_blocking=True
    )
    assert ok["detected"] is True
    assert ok["false_block"] is False
    style = score_seeded_review_detection(
        findings, seeded_paths=["src/app/cache.py"], expect_blocking=False
    )
    assert style["false_block"] is True
