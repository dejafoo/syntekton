"""Deterministic validation pipeline."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from product_factory.domain.findings import ValidatorResult
from product_factory.repositories.patches import apply_patch_check

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
    lower = markdown.lower()
    for section in ARCHITECTURE_REQUIRED_SECTIONS:
        if section.lower() not in lower:
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


def validate_behavioral_commands(
    *,
    repository: Path,
    patch: str,
    command_ids: list[str],
    registered_commands: dict[str, Any],
) -> list[ValidatorResult]:
    """Apply a patch in isolation and execute registered behavioral checks."""
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
            try:
                proc = subprocess.run(
                    [str(spec["executable"]), *map(str, spec.get("args", []))],
                    cwd=work,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=int(spec.get("timeout_seconds", 300)),
                    env={
                        **os.environ,
                        "PYTHONPATH": str(work / "src")
                        + (
                            f":{os.environ['PYTHONPATH']}"
                            if os.environ.get("PYTHONPATH")
                            else ""
                        ),
                    },
                )
                results.append(
                    ValidatorResult(
                        validator_id=f"behavioral:{command_id}",
                        status="pass" if proc.returncode == 0 else "fail",
                        message="ok" if proc.returncode == 0 else "Behavioral command failed",
                        details={
                            "exit_code": proc.returncode,
                            "stdout": proc.stdout[-4000:],
                            "stderr": proc.stderr[-4000:],
                        },
                    )
                )
            except subprocess.TimeoutExpired:
                results.append(
                    ValidatorResult(
                        validator_id=f"behavioral:{command_id}",
                        status="fail",
                        message="Behavioral command timed out",
                    )
                )
        return results


def run_validation_pipeline(checks: list[ValidatorResult]) -> list[ValidatorResult]:
    return checks


def has_blocking_failures(results: list[ValidatorResult]) -> bool:
    return any(r.status in {"fail", "error"} for r in results)
