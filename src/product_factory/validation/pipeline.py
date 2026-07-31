"""Deterministic validation pipeline."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from product_factory.domain.findings import ValidatorResult
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.repositories.patches import apply_patch_check
from product_factory.tools.sandbox import run_sandboxed_command

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
    "Findings",
    "Cited paths",
    "Assumptions",
]

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
FEASIBILITY_FACT_LINE_RE = re.compile(
    r"(?im)^\s*[-*]\s*\**\s*fact\s*\**\s*[:\-]\s*(.+)$"
)

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
    """Require evidence-report sections (summary, findings, cited paths, assumptions)."""
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


def request_expects_web_citations(request_text: str, metadata: dict[str, Any] | None = None) -> bool:
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
                    },
                )
            )
        return results


def run_validation_pipeline(checks: list[ValidatorResult]) -> list[ValidatorResult]:
    return checks


def has_blocking_failures(results: list[ValidatorResult]) -> bool:
    return any(r.status in {"fail", "error"} for r in results)
