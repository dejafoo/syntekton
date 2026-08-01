"""Parser for pytest's stable terminal summary and failure node ids."""

from __future__ import annotations

import re

from product_factory.validation.parsers.base import NormalizedOutcome, ParseResult

_SUMMARY_RE = re.compile(
    r"(?P<count>\d+)\s+"
    r"(?P<status>passed|failed|error|errors|skipped|xfailed|xpassed|warnings?|deselected)"
)
_FAILED_RE = re.compile(r"^FAILED\s+(?P<node>\S+?)(?:\s+-\s+(?P<message>.*))?$")
_ERROR_RE = re.compile(r"^ERROR\s+(?P<node>\S+?)(?:\s+-\s+(?P<message>.*))?$")


def parse_pytest_output(
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
        stripped = line.strip()
        match = _FAILED_RE.match(stripped) or _ERROR_RE.match(stripped)
        if match:
            outcomes.append(
                NormalizedOutcome(
                    kind="test",
                    status="failed",
                    location=match.group("node"),
                    message=(match.group("message") or "pytest reported failure").strip(),
                )
            )

    summary_matches = list(_SUMMARY_RE.finditer(text))
    for match in summary_matches:
        raw_status = match.group("status")
        status = {
            "passed": "passed",
            "failed": "failed",
            "error": "failed",
            "errors": "failed",
            "skipped": "skipped",
            "xfailed": "skipped",
            "xpassed": "warning",
            "warning": "warning",
            "warnings": "warning",
            "deselected": "skipped",
        }[raw_status]
        outcomes.append(
            NormalizedOutcome(
                kind="summary",
                status=status,  # type: ignore[arg-type]
                message=raw_status,
                count=int(match.group("count")),
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
                message="pytest output could not be normalized",
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
                message=f"pytest exited with status {exit_code}",
            )
        )

    return ParseResult(
        parser_id="pytest",
        parser_version="1",
        completeness=completeness,
        outcomes=tuple(outcomes),
        diagnostics=tuple(diagnostics),
    )
