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
