"""Parse and validate evidence-backed independent-review findings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from product_factory.domain.artifacts import ResourceRef
from product_factory.domain.findings import Finding, ValidatorResult

BLOCKING_MIN_CONFIDENCE = 0.7


def evidence_path_resolves(
    evidence_path: str,
    *,
    review_patch: str,
    worktree_root: Path | None,
) -> bool:
    """True when evidence_path appears in the patch or resolves inside the worktree."""
    path = evidence_path.strip()
    if not path:
        return False
    if path in review_patch or f"b/{path}" in review_patch:
        return True
    if worktree_root is None:
        return False
    candidate = worktree_root / path
    try:
        candidate.resolve().relative_to(worktree_root.resolve())
    except ValueError:
        return False
    return candidate.exists()


def normalize_finding_category(raw: str) -> str:
    category = str(raw).strip().lower().replace(" ", "_")
    if category == "testgap":
        category = "test_gap"
    allowed = {
        "correctness",
        "security",
        "maintainability",
        "test_gap",
        "architecture",
        "requirements",
        "policy",
        "evidence",
        "tool_error",
    }
    return category if category in allowed else "correctness"


def normalize_severity(raw: str) -> str:
    severity = str(raw).strip().lower()
    return severity if severity in {"blocking", "major", "minor"} else "major"


def apply_evidence_demotion(
    *,
    severity: str,
    confidence: float,
    evidence_ok: bool,
) -> tuple[str, float]:
    """Demote blocking claims that lack resolvable evidence or sufficient confidence."""
    sev = severity
    conf = float(confidence)
    if not evidence_ok:
        if sev == "blocking":
            sev = "major"
        conf = min(conf, 0.49)
    if sev == "blocking" and conf < BLOCKING_MIN_CONFIDENCE:
        sev = "major"
    return sev, conf


def parse_raw_findings(
    raw_findings: list[dict[str, Any]],
    *,
    task_id: str,
    produced_by: str,
    patch_ref: ResourceRef,
    review_patch: str,
    worktree_root: Path | None,
    acceptance_criterion_ids: list[str] | None = None,
) -> list[Finding]:
    """Build Finding objects from model JSON with evidence demotion applied."""
    findings: list[Finding] = []
    ac_ids = acceptance_criterion_ids or []
    for index, raw in enumerate(raw_findings, 1):
        evidence_path = str(raw.get("evidence_path") or "").strip()
        evidence_ok = evidence_path_resolves(
            evidence_path, review_patch=review_patch, worktree_root=worktree_root
        )
        evidence = patch_ref.model_copy(update={"scope": evidence_path or "patch"})
        confidence = float(raw.get("confidence", 0.5))
        severity = normalize_severity(str(raw.get("severity", "major")))
        category = normalize_finding_category(str(raw.get("category", "correctness")))
        severity, confidence = apply_evidence_demotion(
            severity=severity, confidence=confidence, evidence_ok=evidence_ok
        )
        criterion_id = ac_ids[0] if ac_ids else None
        for ac_id in ac_ids:
            if ac_id in evidence_path or ac_id in str(raw.get("summary", "")):
                criterion_id = ac_id
                break
        findings.append(
            Finding(
                id=f"F-{task_id}-{index}",
                criterion_id=criterion_id,
                category=category,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                summary=str(raw.get("summary") or "Finding"),
                explanation=str(raw.get("explanation") or ""),
                evidence_refs=[evidence],
                recommended_action=str(raw.get("recommended_action") or "") or None,
                confidence=confidence,
                produced_by=produced_by,
            )
        )
    return findings


def validate_review_findings(findings: list[Finding]) -> ValidatorResult:
    """Fail when any open blocking finding lacks a resolvable evidence scope path."""
    bad: list[str] = []
    for finding in findings:
        if finding.status != "open" or finding.severity != "blocking":
            continue
        scopes = [ref.scope for ref in finding.evidence_refs if ref.scope]
        if not scopes or all(scope in {"", "patch"} for scope in scopes):
            # scope "patch" alone is too weak for blocking — require a path-like scope
            if not any("/" in (s or "") or (s or "").endswith(".py") for s in scopes):
                bad.append(finding.id)
                continue
        if finding.confidence < BLOCKING_MIN_CONFIDENCE:
            bad.append(finding.id)
    if bad:
        return ValidatorResult(
            validator_id="review_evidence",
            status="fail",
            message=f"Blocking findings lack resolvable evidence: {bad}",
            details={"finding_ids": bad},
        )
    return ValidatorResult(
        validator_id="review_evidence",
        status="pass",
        message="ok",
        details={"blocking_count": sum(1 for f in findings if f.severity == "blocking")},
    )


def score_seeded_review_detection(
    findings: list[Finding],
    *,
    seeded_paths: list[str],
    expect_blocking: bool,
) -> dict[str, Any]:
    """Evaluate whether review detected a seeded defect (or correctly stayed non-blocking)."""
    blocking = [f for f in findings if f.status == "open" and f.severity == "blocking"]
    cited = False
    for finding in blocking:
        blob = " ".join(
            [
                finding.summary,
                finding.explanation or "",
                *(r.scope or "" for r in finding.evidence_refs),
            ]
        )
        if any(path in blob for path in seeded_paths):
            cited = True
            break
    detected = bool(blocking) and cited
    false_block = bool(blocking) and not expect_blocking
    return {
        "expect_blocking": expect_blocking,
        "blocking_count": len(blocking),
        "detected": detected if expect_blocking else (not false_block),
        "false_block": false_block,
        "cited_seed_path": cited,
    }
