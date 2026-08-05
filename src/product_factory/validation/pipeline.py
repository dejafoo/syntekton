"""Deterministic validation pipeline."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from product_factory.domain.findings import ValidatorResult
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.repositories.patches import apply_patch_check
from product_factory.schemas.validate import validate_write_payload
from product_factory.tools.sandbox import run_sandboxed_command
from product_factory.validation.evidence import write_validation_evidence

SECRET_PATTERNS = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (RSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)api[_-]?key\s*=\s*['\"][^'\"]{8,}['\"]"),
]


ARCHITECTURE_REQUIRED_SECTIONS = [
    "Objective",
    "Scope",
    "Assumptions",
    "Functional requirements",
    "Nonfunctional requirements",
    "Components",
    "Data flows",
    "Security",
    "Testing",
    "Trade-offs",
    "Open questions",
    "Acceptance criteria",
]

INVESTIGATION_REQUIRED_SECTIONS = [
    "Summary",
    "Repository snapshot",
    "Evidence",
    "Findings",
    "Cited paths",
    "Assumptions",
    "Unknowns",
]

TECHNICAL_PLAN_REQUIRED_SECTIONS = [
    *ARCHITECTURE_REQUIRED_SECTIONS,
    "Implementation slices",
    "Verification evidence",
    "Approval items",
]

INVESTIGATION_EVIDENCE_LABELS = frozenset({"fact", "inference", "unknown"})
INVESTIGATION_EVIDENCE_LABEL_RE = re.compile(
    r"(?im)^\s*[-*]\s*\**\s*(fact|inference|unknown)\s*\**\s*[:\-]\s*(.+)$"
)
_AC_ID_RE = re.compile(r"(?i)\bAC[-_ ]?([0-9]+)\b")
_DECISION_ID_RE = re.compile(r"(?i)\bDEC[-_ ]?([0-9]+)\b")
_INVENTED_DEFAULT_RE = re.compile(
    r"(?i)\b(?:we\s+will\s+default|defaults?\s+to|chosen\s+by\s+default|"
    r"assum(?:e|ed|ing)\b.{0,40}\bas\s+(?:the\s+)?default)\b"
)

# WF1 / PM1.D discovery dossier headings (structure only).
FEASIBILITY_REQUIRED_SECTIONS = [
    "Decision",
    "Scope",
    "Domain model",
    "Options",
    "Comparison rubric",
    "Evidence",
    "Assumptions",
    "Unknowns",
    "Risks",
    "Constraints",
    "Recommendation",
    "Next step",
]

FEASIBILITY_RECOMMENDATIONS = frozenset(
    {
        "feasible",
        "feasible_with_constraints",
        "insufficient_evidence",
        "needs_expert_review",
        "not_recommended",
    }
)

FEASIBILITY_EVIDENCE_LABELS = frozenset({"fact", "inference", "assumption", "unknown"})

REGULATED_CLAIM_TOPICS = frozenset({"compliance", "clinical", "legal", "privacy"})

# Source citations in discovery dossiers: source ids, bracket refs, or http(s) URLs.
FEASIBILITY_SOURCE_CITE_RE = re.compile(
    r"(?i)(?:\bsource[_-]?id\b|\bsrc\b)\s*[:=#]?\s*[`'\"]?([A-Za-z0-9_./:-]+)"
    r"|\[(?:src|source)[:\s\-]*([^\]]+)\]"
    r"|(https?://[^\s\]\)>`\"]+)"
)

# Evidence bullet labels required for provenance scoring.
FEASIBILITY_EVIDENCE_LABEL_RE = re.compile(
    r"(?im)^\s*[-*]\s*\**\s*(fact|inference|assumption|unknown)\s*\**\s*[:\-]"
)

# A fact-labeled line without a nearby citation is an unsupported claim.
FEASIBILITY_FACT_LINE_RE = re.compile(r"(?im)^\s*[-*]\s*\**\s*fact\s*\**\s*[:\-]\s*(.+)$")

_OPTION_LINE_RE = re.compile(r"(?im)^\s*[-*]\s*\**\s*option\s+([A-Za-z0-9_-]+)\s*\**\s*[:\-]")
_RUBRIC_CRITERION_RE = re.compile(r"(?im)^\s*[-*]\s+(.+)$")
_EXPERT_REVIEW_RE = re.compile(r"(?im)^\s*expert\s+review\s*:\s*(.+)$")
_JURISDICTION_RE = re.compile(r"(?i)\bjurisdiction\s*[:=]\s*([A-Za-z0-9 _./-]{2,})")
_SOURCE_DATE_RE = re.compile(
    r"(?i)\b(?:source\s+date|published(?:_at)?|as\s+of)\s*[:=]\s*([0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?)"
)
_JURISDICTION_CLAIM_HINTS = (
    "under eu",
    "under us",
    "gdpr",
    "hipaa",
    "in this jurisdiction",
    "lawful basis",
    "data residency requirement",
    "jurisdiction-dependent",
    "jurisdiction dependent",
)

# Path-like citations: backtick-wrapped paths with a slash or file extension.
CITATION_PATH_RE = re.compile(
    r"`((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_./-]+|[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)`"
)

# Fingerprints from the historical deterministic ARCHITECTURE.md template.
ARCHITECTURE_BOILERPLATE_MARKERS = (
    "mvp scope as requested",
    "deliver the requested capabilities",
    "simplicity over premature distribution",
    "exact sla targets",
    "users interact via api/cli; system persists to a database",
    "standard web service deployment",
    "multi-region active-active for mvp",
)

# Minimum substance for a request-specific architecture artifact.
ARCHITECTURE_MIN_CHARS = 1200
ARCHITECTURE_MIN_WORDS = 150

# Optional synonym tokens for soft must-cover matching (topic → acceptable tokens).
MUST_COVER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "tenant isolation": ("tenant isolation", "tenant_id", "row-level", "rls", "tenancy"),
    "multi-tenancy": ("multi-tenant", "multitenant", "tenant isolation", "tenant_id"),
    "invoice lifecycle": ("invoice lifecycle", "draft", "paid", "void", "invoice state"),
    "threat model": ("threat model", "threat modelling", "attack surface", "threat"),
    "data model": ("data model", "schema", "entities", "erd"),
}


def _normalize_heading_text(text: str) -> str:
    """Lowercase alphanumerics only — for soft section / topic matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _heading_present(markdown: str, section: str) -> bool:
    """True if a markdown heading roughly matches the required section name."""
    target = _normalize_heading_text(section)
    if not target:
        return False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        norm = _normalize_heading_text(heading)
        if target in norm or norm in target:
            return True
    # Fallback: normalized section name appears anywhere (legacy exact lower match).
    return _normalize_heading_text(section) in _normalize_heading_text(markdown)


def _topic_covered(markdown: str, topic: str) -> bool:
    """Soft must-cover: full phrase, normalized containment, or synonym tokens."""
    lower = markdown.lower()
    topic_l = topic.lower().strip()
    if not topic_l:
        return True
    if topic_l in lower:
        return True
    if _normalize_heading_text(topic_l) in _normalize_heading_text(markdown):
        return True
    synonyms = MUST_COVER_SYNONYMS.get(topic_l, ())
    for syn in synonyms:
        if syn.lower() in lower:
            return True
    # Token overlap: all significant words (≥4 chars) appear somewhere.
    tokens = [t for t in re.findall(r"[a-z0-9]+", topic_l) if len(t) >= 4]
    return bool(tokens and all(t in lower for t in tokens))


def validate_schema_dict(data: dict[str, Any], required_keys: list[str]) -> ValidatorResult:
    missing = [k for k in required_keys if k not in data]
    if missing:
        return ValidatorResult(
            validator_id="json_schema",
            status="fail",
            message=f"Missing keys: {missing}",
            details={"missing": missing},
        )
    return ValidatorResult(validator_id="json_schema", status="pass", message="ok")


def validate_patch_applies(repository: Path, patch: str) -> ValidatorResult:
    ok = apply_patch_check(repository, patch)
    return ValidatorResult(
        validator_id="patch_applies",
        status="pass" if ok else "fail",
        message="Patch applies" if ok else "Patch does not apply cleanly",
    )


def validate_path_scope(changed_files: list[str], allowed_patterns: list[str]) -> ValidatorResult:
    from product_factory.tools.policies import path_allowed

    bad = [f for f in changed_files if not path_allowed(f, allowed_patterns)]
    if bad:
        return ValidatorResult(
            validator_id="path_scope",
            status="fail",
            message=f"Files outside allowlist: {bad}",
            details={"files": bad},
        )
    return ValidatorResult(validator_id="path_scope", status="pass", message="ok")


def validate_secrets(text: str) -> ValidatorResult:
    hits = []
    for pat in SECRET_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    if hits:
        return ValidatorResult(
            validator_id="secret_scan",
            status="fail",
            message="Possible secrets detected",
            details={"patterns": hits},
        )
    return ValidatorResult(validator_id="secret_scan", status="pass", message="ok")


def validate_architecture_document(markdown: str) -> ValidatorResult:
    missing = []
    for section in ARCHITECTURE_REQUIRED_SECTIONS:
        if not _heading_present(markdown, section):
            missing.append(section)
    # Mermaid parse: crude check for balanced fences
    fences = markdown.count("```mermaid")
    closes = markdown.count("```")
    mermaid_ok = fences == 0 or closes >= fences * 2
    if missing or not mermaid_ok:
        return ValidatorResult(
            validator_id="architecture_sections",
            status="fail",
            message="Architecture document incomplete",
            details={"missing_sections": missing, "mermaid_ok": mermaid_ok},
        )
    return ValidatorResult(validator_id="architecture_sections", status="pass", message="ok")


def validate_investigation_document(markdown: str) -> ValidatorResult:
    """Require the v2 evidence-report sections and snapshot provenance."""
    missing = [
        section
        for section in INVESTIGATION_REQUIRED_SECTIONS
        if not _heading_present(markdown, section)
    ]
    if missing:
        return ValidatorResult(
            validator_id="investigation_sections",
            status="fail",
            message="Evidence report incomplete",
            details={"missing_sections": missing},
        )
    return ValidatorResult(validator_id="investigation_sections", status="pass", message="ok")


def validate_investigation_provenance(markdown: str) -> ValidatorResult:
    """Require fact/inference/unknown labels and provenance for every fact."""
    body = _section_body_exact(markdown, "Evidence")
    unlabeled: list[str] = []
    unsupported_facts: list[str] = []
    labels: list[str] = []
    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped.startswith(("-", "*")):
            continue
        match = INVESTIGATION_EVIDENCE_LABEL_RE.match(stripped)
        if match is None:
            unlabeled.append(stripped[:200])
            continue
        label = match.group(1).lower()
        labels.append(label)
        if label == "fact":
            claim = match.group(2)
            has_path = bool(CITATION_PATH_RE.search(claim))
            has_pin = bool(
                re.search(r"(?i)\b(?:sha256|digest|source)\s*[:=]\s*[a-f0-9]{12,64}\b", claim)
            )
            if not (has_path or has_pin):
                unsupported_facts.append(stripped[:200])
    missing_labels = sorted(INVESTIGATION_EVIDENCE_LABELS - set(labels))
    if not body.strip() or unlabeled or unsupported_facts or missing_labels:
        return ValidatorResult(
            validator_id="investigation_provenance",
            status="fail",
            message="Investigation evidence labeling or provenance incomplete",
            details={
                "unlabeled": unlabeled,
                "unsupported_facts": unsupported_facts,
                "missing_labels": missing_labels,
            },
        )
    return ValidatorResult(
        validator_id="investigation_provenance",
        status="pass",
        message="ok",
        details={"labels": labels},
    )


def _referenced_ids(markdown: str, section: str, pattern: re.Pattern[str]) -> set[str]:
    return {match.group(1) for match in pattern.finditer(_section_body(markdown, section))}


def validate_acceptance_verification_links(markdown: str) -> ValidatorResult:
    """Every acceptance criterion must link to a slice and expected evidence."""
    acceptance = _referenced_ids(markdown, "Acceptance criteria", _AC_ID_RE)
    slices = _referenced_ids(markdown, "Implementation slices", _AC_ID_RE)
    verification = _referenced_ids(markdown, "Verification evidence", _AC_ID_RE)
    problems: list[str] = []
    if not acceptance:
        problems.append("missing_acceptance_ids")
    missing_slices = sorted(acceptance - slices)
    missing_verification = sorted(acceptance - verification)
    unknown_slice_links = sorted(slices - acceptance)
    unknown_verification_links = sorted(verification - acceptance)
    if missing_slices:
        problems.append("acceptance_without_implementation_slice")
    if missing_verification:
        problems.append("acceptance_without_verification_evidence")
    if unknown_slice_links or unknown_verification_links:
        problems.append("links_to_unknown_acceptance")
    status = "fail" if problems else "pass"
    return ValidatorResult(
        validator_id="acceptance_verification_links",
        status=status,
        message="ok" if status == "pass" else "Acceptance-to-verification links incomplete",
        details={
            "problems": problems,
            "acceptance_ids": sorted(acceptance),
            "missing_slices": missing_slices,
            "missing_verification": missing_verification,
            "unknown_slice_links": unknown_slice_links,
            "unknown_verification_links": unknown_verification_links,
        },
    )


def validate_no_invented_defaults(markdown: str) -> ValidatorResult:
    """Unresolved product decisions must be explicit approval items."""
    open_questions = _section_body(markdown, "Open questions")
    approval_items = _section_body(markdown, "Approval items")
    question_ids = _referenced_ids(markdown, "Open questions", _DECISION_ID_RE)
    approval_ids = _referenced_ids(markdown, "Approval items", _DECISION_ID_RE)
    question_bullets = [
        line.strip() for line in open_questions.splitlines() if line.strip().startswith(("-", "*"))
    ]
    unlabeled_questions = [line for line in question_bullets if not _DECISION_ID_RE.search(line)]
    invented = [
        line.strip()[:200]
        for line in (markdown or "").splitlines()
        if _INVENTED_DEFAULT_RE.search(line)
        and not re.search(r"(?i)\b(?:approval|approve|confirm|decision)\b", line)
    ]
    missing_approvals = sorted(question_ids - approval_ids)
    problems: list[str] = []
    if unlabeled_questions:
        problems.append("unlabeled_open_questions")
    if missing_approvals:
        problems.append("open_questions_without_approval_items")
    if invented:
        problems.append("invented_defaults")
    if question_bullets and not approval_items.strip():
        problems.append("missing_approval_items")
    status = "fail" if problems else "pass"
    return ValidatorResult(
        validator_id="no_invented_defaults",
        status=status,
        message="ok" if status == "pass" else "Plan invents or fails to escalate product defaults",
        details={
            "problems": problems,
            "unlabeled_questions": unlabeled_questions,
            "missing_approvals": missing_approvals,
            "invented_defaults": invented,
        },
    )


def extract_feasibility_source_ids(markdown: str) -> list[str]:
    """Collect unique source citation tokens from a feasibility dossier."""
    found: list[str] = []
    seen: set[str] = set()
    for match in FEASIBILITY_SOURCE_CITE_RE.finditer(markdown or ""):
        token = next((g for g in match.groups() if g), "")
        token = token.strip().rstrip(".,;")
        if not token or token.lower() in seen:
            continue
        seen.add(token.lower())
        found.append(token)
    return found


def _section_body(markdown: str, section: str) -> str:
    """Return the body text under a heading that matches `section`."""
    target = _normalize_heading_text(section)
    lines = (markdown or "").splitlines()
    start: int | None = None
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        norm = _normalize_heading_text(heading)
        if target in norm or norm in target:
            start = idx + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for raw in lines[start:]:
        if raw.strip().startswith("#"):
            break
        body.append(raw)
    return "\n".join(body)


def _section_body_exact(markdown: str, section: str) -> str:
    """Return a section body without matching a document title prefix."""
    target = _normalize_heading_text(section)
    lines = (markdown or "").splitlines()
    start: int | None = None
    level = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        if _normalize_heading_text(heading) == target:
            start = idx + 1
            level = len(stripped) - len(stripped.lstrip("#"))
            break
    if start is None:
        return ""
    body: list[str] = []
    for raw in lines[start:]:
        stripped = raw.strip()
        if stripped.startswith("#"):
            next_level = len(stripped) - len(stripped.lstrip("#"))
            if next_level <= level:
                break
        body.append(raw)
    return "\n".join(body)


def _source_record_ids(source_records: list[Any] | None) -> set[str]:
    ids: set[str] = set()
    for record in source_records or []:
        if isinstance(record, dict):
            for key in ("source_id", "id", "sha256", "record_sha256", "source_sha256"):
                value = record.get(key)
                if value:
                    ids.add(str(value).strip().lower())
            continue
        for key in ("source_id", "id", "sha256", "record_sha256", "source_sha256"):
            value = getattr(record, key, None)
            if value:
                ids.add(str(value).strip().lower())
    return ids


def validate_feasibility_document(markdown: str) -> ValidatorResult:
    """Require WF1 dossier headings (Decision through Next step)."""
    missing = [
        section
        for section in FEASIBILITY_REQUIRED_SECTIONS
        if not _heading_present(markdown, section)
    ]
    if missing:
        return ValidatorResult(
            validator_id="feasibility_sections",
            status="fail",
            message="Feasibility dossier incomplete",
            details={"missing_sections": missing},
        )
    return ValidatorResult(validator_id="feasibility_sections", status="pass", message="ok")


def validate_recommendation(markdown: str) -> ValidatorResult:
    """Recommendation value must be one of the WF1 enum tokens."""
    body = _section_body(markdown, "Recommendation") or (markdown or "")
    lower = body.lower()
    hits = [value for value in sorted(FEASIBILITY_RECOMMENDATIONS) if value in lower]
    if not hits:
        return ValidatorResult(
            validator_id="feasibility_recommendation",
            status="fail",
            message="Recommendation must be one of "
            + ", ".join(sorted(FEASIBILITY_RECOMMENDATIONS)),
            details={"found": []},
        )
    return ValidatorResult(
        validator_id="feasibility_recommendation",
        status="pass",
        message="ok",
        details={"found": hits},
    )


# Alias kept for PM1.A eval harness / deterministic checks.
validate_feasibility_recommendation = validate_recommendation


def validate_feasibility_cited_sources(
    markdown: str,
    *,
    minimum: int = 1,
) -> ValidatorResult:
    """Require at least `minimum` distinct source citations."""
    sources = extract_feasibility_source_ids(markdown)
    ok = len(sources) >= max(1, int(minimum))
    return ValidatorResult(
        validator_id="feasibility_cited_sources",
        status="pass" if ok else "fail",
        message="ok" if ok else f"Need at least {minimum} cited sources, found {len(sources)}",
        details={"sources": sources, "minimum": minimum},
    )


def validate_research_provenance(
    markdown: str,
    *,
    source_records: list[Any] | None = None,
) -> ValidatorResult:
    """Evidence entries must be labeled; facts must cite resolvable sources."""
    body = _section_body(markdown, "Evidence")
    if not body.strip():
        return ValidatorResult(
            validator_id="research_provenance",
            status="fail",
            message="Evidence section is empty",
            details={"unlabeled": [], "unresolved_facts": [], "unsupported": []},
        )

    unlabeled: list[str] = []
    unsupported: list[str] = []
    unresolved_facts: list[str] = []
    known_ids = _source_record_ids(source_records)
    check_resolve = source_records is not None and bool(known_ids)

    for raw in body.splitlines():
        stripped = raw.strip()
        if not stripped.startswith(("-", "*")):
            continue
        label_match = FEASIBILITY_EVIDENCE_LABEL_RE.match(stripped)
        if label_match is None:
            lowered = stripped.lower()
            if any(tok in lowered for tok in ("must ", "shall ", "requires ", "official")):
                unlabeled.append(stripped[:200])
            continue
        label = label_match.group(1).lower()
        if label not in FEASIBILITY_EVIDENCE_LABELS:
            unlabeled.append(stripped[:200])
            continue
        if label != "fact":
            continue
        cites = extract_feasibility_source_ids(stripped)
        if not cites:
            unsupported.append(stripped[:200])
            continue
        if check_resolve:
            if not any(cite.lower() in known_ids for cite in cites):
                unresolved_facts.append(stripped[:200])

    if unlabeled or unsupported or unresolved_facts:
        return ValidatorResult(
            validator_id="research_provenance",
            status="fail",
            message=(
                f"{len(unlabeled)} unlabeled, {len(unsupported)} unsupported, "
                f"{len(unresolved_facts)} unresolved fact(s)"
            ),
            details={
                "unlabeled": unlabeled,
                "unsupported": unsupported,
                "unresolved_facts": unresolved_facts,
            },
        )
    return ValidatorResult(
        validator_id="research_provenance",
        status="pass",
        message="ok",
        details={"unlabeled": [], "unsupported": [], "unresolved_facts": []},
    )


def validate_feasibility_unsupported_claims(markdown: str) -> ValidatorResult:
    """Eval-harness helper: fail unlabeled/unsupported evidence claims."""
    result = validate_research_provenance(markdown, source_records=None)
    if result.status == "pass":
        return ValidatorResult(
            validator_id="feasibility_unsupported_claims",
            status="pass",
            message="ok",
            details={"claims": []},
        )
    claims = list(result.details.get("unsupported") or []) + list(
        result.details.get("unlabeled") or []
    )
    return ValidatorResult(
        validator_id="feasibility_unsupported_claims",
        status="fail",
        message=f"{len(claims)} unsupported claim(s)",
        details={"claims": claims},
    )


def validate_option_comparison(markdown: str) -> ValidatorResult:
    """Require ≥2 options, a rubric, and explicit scores/unknowns per criterion."""
    options_body = _section_body(markdown, "Options")
    rubric_body = _section_body(markdown, "Comparison rubric")
    option_ids = [match.group(1) for match in _OPTION_LINE_RE.finditer(options_body)]
    if len(option_ids) < 2:
        # Fall back to counting distinct option bullets when the "Option X:" form is absent.
        bullets = [
            line.strip()
            for line in options_body.splitlines()
            if line.strip().startswith(("-", "*"))
        ]
        option_ids = [f"opt-{idx}" for idx, _ in enumerate(bullets, start=1)]
    criteria = [
        match.group(1).strip().rstrip(".")
        for match in _RUBRIC_CRITERION_RE.finditer(rubric_body)
        if match.group(1).strip()
    ]
    if not criteria and rubric_body.strip():
        # Comma/semicolon separated rubric on one line is acceptable.
        flat = re.sub(r"(?i)^comparison rubric:?\s*", "", rubric_body.strip())
        criteria = [part.strip() for part in re.split(r"[,;/]| and ", flat) if part.strip()]

    problems: list[str] = []
    if len(option_ids) < 2:
        problems.append("need_at_least_two_options")
    if not criteria:
        problems.append("missing_comparison_rubric")

    # Scoring may live under Options, Comparison rubric, or a later matrix table.
    scoring_corpus = "\n".join(
        [
            options_body,
            rubric_body,
            _section_body(markdown, "Constraints"),
            markdown or "",
        ]
    ).lower()
    missing_scores: list[str] = []
    for option in option_ids:
        option_key = option.lower()
        for criterion in criteria:
            crit_key = criterion.lower()
            # Accept either an explicit "option / criterion: score|unknown" mention
            # or an "unknown" cell near both tokens.
            paired = (
                option_key in scoring_corpus
                and crit_key in scoring_corpus
                and (
                    "unknown" in scoring_corpus
                    or "score" in scoring_corpus
                    or re.search(
                        rf"{re.escape(option_key)}.{{0,80}}{re.escape(crit_key)}|"
                        rf"{re.escape(crit_key)}.{{0,80}}{re.escape(option_key)}",
                        scoring_corpus,
                        flags=re.I | re.S,
                    )
                )
            )
            if not paired:
                missing_scores.append(f"{option}/{criterion}")

    # Soften: when options and rubric exist and the dossier mentions unknown
    # cells (or "scored"), treat the comparison as structurally present for PM1.
    if missing_scores and (
        "unknown" in scoring_corpus
        or "scored" in scoring_corpus
        or "score" in scoring_corpus
        or "comparison" in scoring_corpus
    ):
        missing_scores = []

    if problems or missing_scores:
        return ValidatorResult(
            validator_id="option_comparison",
            status="fail",
            message="Option comparison incomplete",
            details={
                "problems": problems,
                "options": option_ids,
                "criteria": criteria,
                "missing_scores": missing_scores[:20],
            },
        )
    return ValidatorResult(
        validator_id="option_comparison",
        status="pass",
        message="ok",
        details={"options": option_ids, "criteria": criteria},
    )


def validate_regulated_claims(
    markdown: str,
    *,
    policy: Any | None = None,
) -> ValidatorResult:
    """Regulated verdicts require expert-review escalation; jurisdiction needs date."""
    text = markdown or ""
    lower = text.lower()
    recommendation = validate_recommendation(text)
    rec_values = set(recommendation.details.get("found") or [])
    require_topics = {
        str(t).strip().lower()
        for t in (getattr(policy, "require_expert_review_for", None) or [])
        if str(t).strip()
    }
    # Rubric criteria such as "security/privacy" are not regulated verdicts.
    # Require claim-like phrasing around the topic before escalating.
    topics_hit: list[str] = []
    for topic in sorted(REGULATED_CLAIM_TOPICS | require_topics):
        if re.search(
            rf"(?i)\b{re.escape(topic)}\b.{{0,40}}\b(verdict|approval|clearance|compliant|"
            rf"lawful|certified|conclusion|ruling)\b"
            rf"|\b(verdict|approval|clearance|compliant|lawful|certified|conclusion|"
            rf"ruling)\b.{{0,40}}\b{re.escape(topic)}\b",
            text,
        ):
            topics_hit.append(topic)
            continue
        # Explicit "Compliance: ..." / "Clinical claim:" style labels.
        if re.search(rf"(?im)^\s*[-*]?\s*\**{re.escape(topic)}\**\s*:\s+\S+", text):
            topics_hit.append(topic)

    problems: list[str] = []
    expert_line = _EXPERT_REVIEW_RE.search(text)
    if topics_hit:
        if expert_line is None:
            problems.append("missing_expert_review_line")
        allowed = {"needs_expert_review", "insufficient_evidence"}
        if not (rec_values & allowed):
            problems.append("regulated_recommendation_must_escalate")

    # Jurisdiction-dependent claims need an explicit jurisdiction and source date.
    jurisdiction_claim = any(hint in lower for hint in _JURISDICTION_CLAIM_HINTS)
    if jurisdiction_claim:
        has_jurisdiction = bool(_JURISDICTION_RE.search(text)) or bool(
            re.search(r"(?im)^\s*[-*].*\bjurisdiction\b", text)
        )
        has_date = bool(_SOURCE_DATE_RE.search(text))
        if not has_jurisdiction or not has_date:
            problems.append("jurisdiction_claim_missing_jurisdiction_or_source_date")

    if problems:
        return ValidatorResult(
            validator_id="regulated_claims_review",
            status="fail",
            message="Regulated claims review failed",
            details={
                "problems": problems,
                "topics": topics_hit,
                "recommendation": sorted(rec_values),
                "expert_review": expert_line.group(1).strip() if expert_line else None,
            },
        )
    return ValidatorResult(
        validator_id="regulated_claims_review",
        status="pass",
        message="ok",
        details={"topics": topics_hit, "recommendation": sorted(rec_values)},
    )


# WF2 / PM2.A change_intake headings (structure only).
INTAKE_BRIEF_REQUIRED_SECTIONS = [
    "Outcome",
    "Scope",
    "Non-goals",
    "Acceptance criteria",
    "Constraints",
    "Risks",
    "Assumptions",
    "Unknowns",
    "Recommended next pack",
]

INTAKE_CLARIFICATION_REQUIRED_SECTIONS = [
    "Questions",
    "Blocking unknowns",
    "Partial outcome",
]

_AMBIGUOUS_REQUEST_MARKERS = (
    "something",
    "somehow",
    "maybe",
    "not sure",
    "improve things",
    "make it better",
    "figure out",
    "whatever",
    "unclear",
    "vague",
    "tbd",
    "??? ",
    "???",
)

_WELL_SCOPED_REQUEST_MARKERS = (
    "acceptance criteria",
    "acceptance:",
    "must ",
    "should ",
    "add endpoint",
    "fix bug",
    "defect:",
    "implement ",
    "when users",
    "given that",
    "non-goals",
    "out of scope",
)


def request_looks_underspecified(request_text: str, *, pack_input: dict | None = None) -> bool:
    """Heuristic used by intake validators and deterministic compose (fixture-driven)."""
    text = (request_text or "").strip().lower()
    payload = pack_input or {}
    desired = str(payload.get("desired_outcome") or "").strip()
    constraints = payload.get("known_constraints") or []
    has_typed_scope = bool(desired) and bool(constraints)
    if any(marker in text for marker in _AMBIGUOUS_REQUEST_MARKERS):
        return True
    if len(text) < 40 and not has_typed_scope:
        return True
    if any(marker in text for marker in _WELL_SCOPED_REQUEST_MARKERS):
        return False
    if has_typed_scope:
        return False
    # Default: treat short/underspecified prose without scoping cues as needing clarification.
    return len(text.split()) < 25


def validate_intake_sections(
    markdown: str,
    *,
    role: str,
) -> ValidatorResult:
    """Require brief or clarification headings for the primary intake landable."""
    from product_factory.workflows.artifacts import ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST

    if role == ROLE_CLARIFICATION_REQUEST:
        required = INTAKE_CLARIFICATION_REQUIRED_SECTIONS
    else:
        required = INTAKE_BRIEF_REQUIRED_SECTIONS
        role = ROLE_CHANGE_BRIEF
    missing = [section for section in required if not _heading_present(markdown, section)]
    if missing:
        return ValidatorResult(
            validator_id="intake_sections",
            status="fail",
            message=f"Intake {role} incomplete",
            details={"missing_sections": missing, "role": role},
        )
    return ValidatorResult(
        validator_id="intake_sections",
        status="pass",
        message="ok",
        details={"role": role},
    )


def validate_intake_no_invention(
    markdown: str,
    *,
    role: str,
    request_text: str = "",
    pack_input: dict | None = None,
) -> ValidatorResult:
    """Reject invented confidence: clarification must not look like a full brief;
    a brief for an under-specified request must keep unknowns explicit.
    """
    from product_factory.workflows.artifacts import ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST

    problems: list[str] = []
    body = markdown or ""
    lower = body.lower()
    underspecified = request_looks_underspecified(request_text, pack_input=pack_input)

    if role == ROLE_CLARIFICATION_REQUEST:
        # A clarification that invents a complete acceptance set is overconfident.
        acceptance = _section_body(body, "Acceptance criteria")
        if (
            acceptance.strip()
            and len(
                [
                    line
                    for line in acceptance.splitlines()
                    if line.strip().startswith(("-", "*", "1."))
                ]
            )
            >= 3
        ):
            problems.append("clarification_invented_acceptance_set")
        if _heading_present(body, "Acceptance criteria") and not _heading_present(
            body, "Questions"
        ):
            problems.append("clarification_missing_questions")
        questions = _section_body(body, "Questions")
        if not any(
            line.strip().startswith(("-", "*", "1.", "?")) or "?" in line
            for line in questions.splitlines()
            if line.strip()
        ):
            if not questions.strip():
                problems.append("clarification_empty_questions")
    elif role == ROLE_CHANGE_BRIEF:
        unknowns = _section_body(body, "Unknowns")
        unknown_lines = [
            line
            for line in unknowns.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        empty_unknowns = not unknown_lines or all(
            line.strip().lower() in {"- none", "- none.", "none", "n/a", "- n/a"}
            for line in unknown_lines
        )
        if underspecified and empty_unknowns:
            problems.append("brief_empty_unknowns_for_underspecified_request")
        if "recommended next pack" in lower and "repository_change" in lower and underspecified:
            problems.append("brief_recommends_change_for_underspecified_request")

    if problems:
        return ValidatorResult(
            validator_id="intake_no_invention",
            status="fail",
            message="Intake over-confident or invented framing",
            details={"problems": problems, "role": role, "underspecified": underspecified},
        )
    return ValidatorResult(
        validator_id="intake_no_invention",
        status="pass",
        message="ok",
        details={"role": role, "underspecified": underspecified},
    )


def validate_document_sections(
    markdown: str,
    *,
    validator_id: str,
    required_sections: list[str] | tuple[str, ...],
) -> ValidatorResult:
    """Require a set of headings in a composed markdown deliverable.

    Section names come from the workflow pack, so a pack can add a deliverable
    shape without a new validator here. Matching is on heading text, never on the
    filename, so a renamed deliverable validates identically.
    """
    missing = [section for section in required_sections if not _heading_present(markdown, section)]
    if missing:
        return ValidatorResult(
            validator_id=validator_id,
            status="fail",
            message=f"Document missing required sections: {missing}",
            details={"missing_sections": missing},
        )
    return ValidatorResult(validator_id=validator_id, status="pass", message="ok")


def validate_json_contract(
    document: str,
    *,
    schema_id: str,
    validator_id: str,
) -> ValidatorResult:
    """Parse and machine-validate a JSON task-output contract."""
    try:
        payload = json.loads(document)
        validate_write_payload(schema_id, payload)
    except Exception as exc:
        return ValidatorResult(
            validator_id=validator_id,
            status="fail",
            message=f"Invalid {schema_id} payload",
            details={"error": str(exc)},
        )
    return ValidatorResult(
        validator_id=validator_id,
        status="pass",
        message="ok",
        details={"schema_id": schema_id},
    )


def validate_verification_report(document: str) -> ValidatorResult:
    """Require typed, unique pass/fail/gap mappings consistent with the outcome."""
    contract = validate_json_contract(
        document,
        schema_id="verification_report.v1",
        validator_id="verification_report_contract",
    )
    if contract.status != "pass":
        return contract
    payload = json.loads(document)
    results = payload.get("acceptance_results") or []
    refs = [str(item.get("acceptance_ref") or "") for item in results]
    statuses = [str(item.get("status") or "") for item in results]
    errors: list[str] = []
    if any(not ref for ref in refs):
        errors.append("acceptance_result missing acceptance_ref")
    if len(refs) != len(set(refs)):
        errors.append("acceptance_ref entries must be unique")
    invalid = sorted(set(statuses) - {"pass", "fail", "gap"})
    if invalid:
        errors.append(f"invalid acceptance statuses: {invalid}")
    outcome = payload.get("outcome")
    if "fail" in statuses and outcome != "blocked":
        errors.append("failed acceptance criteria require blocked outcome")
    if "gap" in statuses and outcome != "insufficient_evidence":
        errors.append("acceptance gaps require insufficient_evidence outcome")
    if not results and outcome not in {"blocked", "insufficient_evidence"}:
        errors.append("empty acceptance mapping cannot produce a passing outcome")
    if outcome in {"passes", "passes_with_risk"} and any(status != "pass" for status in statuses):
        errors.append("passing outcomes require all acceptance criteria to pass")
    if errors:
        return ValidatorResult(
            validator_id="verification_report_contract",
            status="fail",
            message="VerificationReport acceptance mapping is inconsistent",
            details={"errors": errors},
        )
    return contract


def validate_release_plan(document: str) -> ValidatorResult:
    """Validate the PM5.A outcome and evidence-binding invariants."""
    contract = validate_json_contract(
        document,
        schema_id="release_plan.v1",
        validator_id="release_plan_contract",
    )
    if contract.status != "pass":
        return contract
    payload = json.loads(document)
    errors: list[str] = []
    outcome = str(payload.get("outcome") or "")
    if outcome not in {"ready", "blocked", "needs_decision"}:
        errors.append(f"invalid outcome: {outcome!r}")
    digests = payload.get("input_digests") or {}
    if not isinstance(digests, dict) or not digests:
        errors.append("input_digests must be a non-empty object")
        digest_keys: set[str] = set()
    else:
        digest_keys = {str(key) for key in digests}
        invalid_digests = [
            str(key)
            for key, value in digests.items()
            if re.fullmatch(r"[0-9a-f]{64}", str(value)) is None
        ]
        if invalid_digests:
            errors.append(f"invalid input digests: {sorted(invalid_digests)}")
    verification = payload.get("verification_evidence") or payload.get("evidence_refs") or []
    migration = payload.get("migration_preconditions") or []
    rollback = payload.get("rollback_criteria") or []
    decisions = payload.get("unresolved_decisions") or []
    if outcome == "ready":
        if not verification:
            errors.append("ready requires verification evidence")
        if not migration:
            errors.append("ready requires migration evidence or explicit not-required precondition")
        if not rollback:
            errors.append("ready requires rollback criteria")
        if decisions:
            errors.append("ready cannot contain unresolved decisions")
    if outcome == "needs_decision" and not decisions:
        errors.append("needs_decision requires unresolved_decisions")
    for index, claim in enumerate(payload.get("claims") or []):
        refs = set(str(item) for item in (claim.get("input_digest_refs") or []))
        if not refs or not refs <= digest_keys:
            errors.append(f"claim {index} does not pin known input digests")
    if not payload.get("claims"):
        errors.append("release claims must pin input digests")
    if errors:
        return ValidatorResult(
            validator_id="release_plan_contract",
            status="fail",
            message="ReleasePlan readiness contract is inconsistent",
            details={"errors": errors},
        )
    return contract


def validate_operational_record(document: str) -> ValidatorResult:
    """Validate PM5.D evidence labels, follow-up typing, and read-only authority."""
    contract = validate_json_contract(
        document,
        schema_id="operational_record.v1",
        validator_id="operational_record_contract",
    )
    if contract.status != "pass":
        return contract
    payload = json.loads(document)
    errors: list[str] = []
    evidence = payload.get("evidence") or []
    hypotheses = payload.get("hypotheses") or []
    timeline = payload.get("timeline") or []
    if any(item.get("label") != "observation" for item in evidence if isinstance(item, dict)):
        errors.append("every evidence item must be labeled observation")
    if any(item.get("label") != "inference" for item in hypotheses if isinstance(item, dict)):
        errors.append("every hypothesis must be labeled inference")
    if any(item.get("label") != "observation" for item in timeline if isinstance(item, dict)):
        errors.append("every timeline item must be labeled observation")

    follow_up = str(payload.get("follow_up") or "")
    action = payload.get("follow_up_action") or {}
    if action.get("type") != follow_up:
        errors.append("follow_up_action.type must match follow_up")
    if follow_up in {"rollback_decision", "human_escalation"} and not action.get("requires_human"):
        errors.append(f"{follow_up} must require a human")

    authority = payload.get("authority") or {}
    if authority.get("class") != "external_read":
        errors.append("operational records require external_read authority")
    for effect in ("deploy", "restart", "traffic_mutation"):
        if authority.get(effect) is not False:
            errors.append(f"operational authority must explicitly deny {effect}")

    if errors:
        return ValidatorResult(
            validator_id="operational_record_contract",
            status="fail",
            message="OperationalRecord evidence or authority contract is inconsistent",
            details={"errors": errors},
        )
    return contract


def validate_deployment_record(document: str) -> ValidatorResult:
    """Validate immutable approval binding and fail-safe deployment receipts."""
    contract = validate_json_contract(
        document,
        schema_id="deployment_record.v1",
        validator_id="deployment_record_contract",
    )
    if contract.status != "pass":
        return contract
    payload = json.loads(document)
    errors: list[str] = []
    binding = payload.get("approval_binding") or {}
    for field in ("release_plan_digest", "artifact_digest", "target_id"):
        if not binding or str(binding.get(field) or "") != str(payload.get(field) or ""):
            errors.append(f"approval binding does not match {field}")
    if not str(binding.get("approval_id") or ""):
        errors.append("approval binding requires approval_id")
    if binding.get("change_window") != payload.get("change_window"):
        errors.append("approval binding does not match change_window")
    if str(payload.get("environment") or "").lower() in {"prod", "production"}:
        errors.append("production deployment records are prohibited")
    if not str(payload.get("idempotency_key") or ""):
        errors.append("idempotency_key is required")
    outcome = str(payload.get("outcome") or "")
    if outcome in {"halted", "failed"} and not payload.get("rollback_result"):
        errors.append("failed or halted deployment requires a rollback result")
    if outcome == "succeeded" and not payload.get("health_checks"):
        errors.append("succeeded deployment requires health checks")
    if not payload.get("action_log"):
        errors.append("deployment action_log must not be empty")
    if errors:
        return ValidatorResult(
            validator_id="deployment_record_contract",
            status="fail",
            message="DeploymentRecord change-control contract is inconsistent",
            details={"errors": errors},
        )
    return contract


def validate_citations(markdown: str) -> ValidatorResult:
    """Require at least one path-like citation in backtick form."""
    citations = sorted({match.group(1) for match in CITATION_PATH_RE.finditer(markdown or "")})
    if not citations:
        return ValidatorResult(
            validator_id="citation_presence",
            status="fail",
            message="Evidence report must cite at least one repository path",
            details={"citations": []},
        )
    return ValidatorResult(
        validator_id="citation_presence",
        status="pass",
        message="ok",
        details={"citations": citations},
    )


_WEB_CITATION_HINTS = (
    "citation",
    "citations",
    "cite sources",
    "cite urls",
    "web search",
    "search the web",
    "with sources",
    "official docs",
    "official documentation",
)


def request_expects_web_citations(
    request_text: str, metadata: dict[str, Any] | None = None
) -> bool:
    """True when the host asked for web-backed sources / citations."""
    meta = metadata or {}
    flag = str(meta.get("require_web_search") or meta.get("require_web_citations") or "").strip()
    if flag.lower() in {"1", "true", "yes", "on"}:
        return True
    text = (request_text or "").lower()
    return any(hint in text for hint in _WEB_CITATION_HINTS)


def validate_web_search_used(
    *,
    expected: bool,
    connector_enabled: bool,
    invocation_count: int,
    connector_id: str = "tavily_web_search",
) -> ValidatorResult | None:
    """Fail when web citations were requested but the search connector never ran.

    Skips when the request did not ask for web sources, or when the connector is
    disabled (research then cannot search even if the model invents URLs).
    """
    if not expected or not connector_enabled:
        return None
    if invocation_count >= 1:
        return ValidatorResult(
            validator_id="web_search_used",
            status="pass",
            message="ok",
            details={"connector_id": connector_id, "invocation_count": invocation_count},
        )
    return ValidatorResult(
        validator_id="web_search_used",
        status="fail",
        message=(
            "Request asked for web citations/sources but "
            f"{connector_id!r} was never invoked (no connector.invoked events). "
            "Ensure TAVILY_API_KEY is set and research tasks call web_search."
        ),
        details={"connector_id": connector_id, "invocation_count": 0},
    )


def validate_architecture_request_specificity(
    markdown: str,
    *,
    must_cover: list[str] | None = None,
    reject_boilerplate: bool = True,
) -> list[ValidatorResult]:
    """Fail empty, near-empty, pure templates, and missing request-specific topics."""
    results: list[ValidatorResult] = []
    text = (markdown or "").strip()
    lower = text.lower()
    words = re.findall(r"[a-z0-9]+", lower)

    if len(text) < ARCHITECTURE_MIN_CHARS or len(words) < ARCHITECTURE_MIN_WORDS:
        results.append(
            ValidatorResult(
                validator_id="architecture_substance",
                status="fail",
                message=(
                    "Architecture artifact too thin for request-specific review "
                    f"(chars={len(text)}, words={len(words)}; "
                    f"need ≥{ARCHITECTURE_MIN_CHARS} chars and ≥{ARCHITECTURE_MIN_WORDS} words)"
                ),
                details={"chars": len(text), "words": len(words)},
            )
        )
    else:
        results.append(
            ValidatorResult(
                validator_id="architecture_substance",
                status="pass",
                message="ok",
                details={"chars": len(text), "words": len(words)},
            )
        )

    if reject_boilerplate:
        hits = [marker for marker in ARCHITECTURE_BOILERPLATE_MARKERS if marker in lower]
        if hits:
            results.append(
                ValidatorResult(
                    validator_id="architecture_boilerplate",
                    status="fail",
                    message="Architecture looks like a generic template, not request-specific",
                    details={"boilerplate_markers": hits},
                )
            )
        else:
            results.append(
                ValidatorResult(
                    validator_id="architecture_boilerplate",
                    status="pass",
                    message="ok",
                )
            )

    topics = [str(item).strip() for item in (must_cover or []) if str(item).strip()]
    if topics:
        missing = [topic for topic in topics if not _topic_covered(text, topic)]
        results.append(
            ValidatorResult(
                validator_id="architecture_must_cover",
                status="pass" if not missing else "fail",
                message="ok" if not missing else f"Missing must-cover topics: {missing}",
                details={"required": topics, "missing": missing},
            )
        )
    return results


def validate_behavioral_commands(
    *,
    repository: Path,
    patch: str,
    command_ids: list[str],
    registered_commands: dict[str, Any],
    ledger: BudgetLedger | None = None,
    artifact_store: ArtifactStore | None = None,
    created_by_task_id: str = "behavioral-validation",
    input_revision: str = "worktree",
    profile_version: str = "registered-commands.v1",
    validation_baselines: dict[str, str] | None = None,
) -> list[ValidatorResult]:
    """Apply a patch in isolation and execute registered behavioral checks.

    Commands run through `tools/sandbox.run_sandboxed_command` (restricted
    subprocess + optional bwrap) rather than raw `subprocess` with ambient
    env (P1.D) — no inherited secrets, hard timeout, worktree-confined cwd.
    Unknown command ids fail closed; there is no host-shell fallback.
    """
    if not command_ids:
        return []
    with tempfile.TemporaryDirectory(prefix="pf-validation-") as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(repository, work, symlinks=True)
        apply = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=work,
            input=patch,
            text=True,
            capture_output=True,
            check=False,
        )
        if apply.returncode:
            return [
                ValidatorResult(
                    validator_id="behavioral_setup",
                    status="fail",
                    message="Patch could not be applied for behavioral validation",
                    details={"stderr": apply.stderr[-2000:]},
                )
            ]
        results: list[ValidatorResult] = []
        for command_id in command_ids:
            spec = registered_commands.get(command_id)
            if not spec:
                results.append(
                    ValidatorResult(
                        validator_id=f"behavioral:{command_id}",
                        status="fail",
                        message=f"Unknown registered command: {command_id}",
                    )
                )
                continue
            timeout_seconds = int(spec.get("timeout_seconds", 300))
            if ledger is not None:
                ledger.check_before_command(timeout_seconds=timeout_seconds)
            sandbox_result = run_sandboxed_command(
                executable=str(spec["executable"]),
                args=[str(a) for a in spec.get("args", [])],
                cwd=work,
                timeout_seconds=timeout_seconds,
                pythonpath=str(work / "src"),
            )
            if ledger is not None:
                ledger.record_command(duration_seconds=sandbox_result.duration_seconds)
            timed_out = sandbox_result.returncode == 124
            evidence_details: dict[str, Any] = {}
            if artifact_store is not None:
                evidence = write_validation_evidence(
                    artifact_store=artifact_store,
                    command_id=command_id,
                    registered_command_ids=set(registered_commands),
                    stdout=sandbox_result.stdout,
                    stderr=sandbox_result.stderr,
                    exit_code=sandbox_result.returncode,
                    input_revision=input_revision,
                    created_by_task_id=created_by_task_id,
                    sandbox=sandbox_result.sandbox,
                    duration_seconds=sandbox_result.duration_seconds,
                    profile_version=profile_version,
                    previous_evidence_ref=(validation_baselines or {}).get(command_id),
                )
                evidence_details = {
                    "validation_evidence_ref": evidence.artifact_ref.sha256,
                    "validation_raw_ref": evidence.raw_ref.sha256,
                    "normalized_outcomes": evidence.payload["normalized_outcomes"],
                    "baseline_comparison": evidence.payload["baseline_comparison"],
                }
            results.append(
                ValidatorResult(
                    validator_id=f"behavioral:{command_id}",
                    status="pass" if sandbox_result.returncode == 0 else "fail",
                    message=(
                        "ok"
                        if sandbox_result.returncode == 0
                        else "Behavioral command timed out"
                        if timed_out
                        else "Behavioral command failed"
                    ),
                    details={
                        "exit_code": sandbox_result.returncode,
                        "stdout": sandbox_result.stdout[-4000:],
                        "stderr": sandbox_result.stderr[-4000:],
                        "sandbox": sandbox_result.sandbox,
                        **evidence_details,
                    },
                )
            )
        return results


def run_validation_pipeline(checks: list[ValidatorResult]) -> list[ValidatorResult]:
    return checks


def has_blocking_failures(results: list[ValidatorResult]) -> bool:
    return any(r.status in {"fail", "error"} for r in results)
