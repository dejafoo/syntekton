"""Shared normalized validation-output contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

OutcomeStatus = Literal["passed", "failed", "warning", "skipped", "unknown"]
ParseCompleteness = Literal["complete", "partial", "malformed"]


@dataclass(frozen=True)
class NormalizedOutcome:
    """One tool-independent test or diagnostic outcome."""

    kind: Literal["test", "diagnostic", "summary", "parser"]
    status: OutcomeStatus
    message: str
    location: str | None = None
    code: str | None = None
    count: int | None = None

    def as_payload(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class ParseResult:
    parser_id: str
    parser_version: str
    completeness: ParseCompleteness
    outcomes: tuple[NormalizedOutcome, ...] = ()
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def normalized_outcomes(self) -> list[dict[str, Any]]:
        return [outcome.as_payload() for outcome in self.outcomes]


def parse_validation_output(
    command_id: str,
    *,
    stdout: str,
    stderr: str,
    exit_code: int,
    truncated: bool = False,
) -> ParseResult:
    """Parse output only for an explicitly supported registered command id."""

    if command_id == "python_tests":
        from product_factory.validation.parsers.pytest import parse_pytest_output

        return parse_pytest_output(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            truncated=truncated,
        )
    if command_id == "python_typecheck":
        from product_factory.validation.parsers.basedpyright import parse_basedpyright_output

        return parse_basedpyright_output(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            truncated=truncated,
        )
    return ParseResult(
        parser_id="unsupported",
        parser_version="1",
        completeness="malformed",
        outcomes=(
            NormalizedOutcome(
                kind="parser",
                status="unknown",
                message=f"No validation parser registered for command {command_id!r}",
            ),
        ),
        diagnostics=("unsupported_command_parser",),
    )
