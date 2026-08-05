"""Parser for basedpyright text output."""

from __future__ import annotations

import re

from product_factory.validation.parsers.base import NormalizedOutcome, ParseResult

_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<column>\d+)\s+-\s+"
    r"(?P<severity>error|warning|information):\s+"
    r"(?P<message>.*?)(?:\s+\((?P<code>[^()]+)\))?$"
)
_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+(?P<severity>errors?|warnings?|notes?|informations?)\b",
    re.IGNORECASE,
)


def parse_basedpyright_output(
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    truncated: bool = False,
) -> ParseResult:
    text = "\n".join(part for part in (stdout, stderr) if part)
    outcomes: list[NormalizedOutcome] = []
    diagnostics: list[str] = []

    for line in text.splitlines():
        match = _DIAGNOSTIC_RE.match(line.strip())
        if not match:
            continue
        severity = match.group("severity")
        status = "failed" if severity == "error" else "warning"
        outcomes.append(
            NormalizedOutcome(
                kind="diagnostic",
                status=status,  # type: ignore[arg-type]
                location=(f"{match.group('path')}:{match.group('line')}:{match.group('column')}"),
                code=match.group("code"),
                message=match.group("message").strip(),
            )
        )

    summary_matches = list(_SUMMARY_RE.finditer(text))
    for match in summary_matches:
        severity = match.group("severity").lower()
        count = int(match.group("count"))
        if severity.startswith("error"):
            status = "failed" if count else "passed"
        elif severity.startswith("warning"):
            status = "warning" if count else "passed"
        else:
            status = "passed"
        outcomes.append(
            NormalizedOutcome(
                kind="summary",
                status=status,  # type: ignore[arg-type]
                message=severity,
                count=count,
            )
        )

    if truncated:
        diagnostics.append("truncated_output")
    if not summary_matches:
        diagnostics.append("missing_terminal_summary")
    if not text.strip():
        diagnostics.append("empty_output")

    if not outcomes:
        completeness = "malformed"
        outcomes.append(
            NormalizedOutcome(
                kind="parser",
                status="unknown",
                message="basedpyright output could not be normalized",
            )
        )
    elif truncated or not summary_matches:
        completeness = "partial"
    else:
        completeness = "complete"

    if exit_code != 0 and not any(outcome.status == "failed" for outcome in outcomes):
        outcomes.append(
            NormalizedOutcome(
                kind="summary",
                status="failed",
                message=f"basedpyright exited with status {exit_code}",
            )
        )

    return ParseResult(
        parser_id="basedpyright",
        parser_version="1",
        completeness=completeness,
        outcomes=tuple(outcomes),
        diagnostics=tuple(diagnostics),
    )
