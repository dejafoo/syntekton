"""Repair planning and no-progress detection."""

from __future__ import annotations

import hashlib
import json

from product_factory.domain.budgets import TaskBudget
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec


def create_repair_tasks(
    *,
    failures: list[ValidatorResult],
    findings: list[Finding],
    originating_task_id: str,
    allowed_path_patterns: list[str],
    next_id_start: int = 1,
) -> list[TaskSpec]:
    repairs: list[TaskSpec] = []
    idx = next_id_start
    for fail in failures:
        if fail.status not in {"fail", "error"}:
            continue
        tid = f"R-{idx:03d}"
        idx += 1
        repairs.append(
            TaskSpec(
                id=tid,
                title=f"Repair: {fail.validator_id}",
                capability="repair",
                objective=(
                    f"Fix validation failure {fail.validator_id}: {fail.message}\n"
                    f"Failure evidence:\n{json.dumps(fail.details, indent=2, default=str)}"
                ),
                rationale=f"Targeted repair for {fail.validator_id}",
                dependencies=[originating_task_id],
                expected_output_schema="repair_result.v1",
                required_tool_classes={"repository_read", "repository_write", "git_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id=f"{tid}-AC1",
                        description=f"Re-validate {fail.validator_id}",
                        verification="artifact_check",
                        severity="blocking",
                    )
                ],
                allowed_path_patterns=allowed_path_patterns,
                budget=TaskBudget(
                    max_input_tokens=32_000,
                    max_output_tokens=8_000,
                    max_tool_calls=15,
                    max_repair_attempts=1,
                    max_wall_clock_seconds=600,
                ),
            )
        )
    for finding in findings:
        if finding.severity != "blocking" or finding.status != "open":
            continue
        tid = f"R-{idx:03d}"
        idx += 1
        repairs.append(
            TaskSpec(
                id=tid,
                title=f"Repair finding: {finding.summary[:60]}",
                capability="repair",
                objective=finding.recommended_action or finding.explanation,
                rationale=finding.summary,
                dependencies=[originating_task_id],
                expected_output_schema="repair_result.v1",
                required_tool_classes={"repository_read", "repository_write", "git_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id=f"{tid}-AC1",
                        description=f"Resolve finding {finding.id}",
                        verification="llm_review",
                        severity="blocking",
                    )
                ],
                allowed_path_patterns=allowed_path_patterns,
            )
        )
    return repairs


def patch_fingerprint(patch: str) -> str:
    # Normalize whitespace-ish noise lightly
    normalized = "\n".join(line.rstrip() for line in patch.splitlines())
    return hashlib.sha256(normalized.encode()).hexdigest()


def update_no_progress(
    *,
    no_progress_count: int,
    previous_findings: list[str],
    current_findings: list[str],
    previous_patch_fp: str | None,
    current_patch_fp: str | None,
    criterion_improved: bool,
) -> tuple[int, str | None]:
    reason = None
    bumped = False
    if previous_findings and set(previous_findings) == set(current_findings) and current_findings:
        no_progress_count += 1
        bumped = True
        reason = "same_blocking_findings"
    if (
        previous_patch_fp
        and current_patch_fp
        and previous_patch_fp == current_patch_fp
        and not criterion_improved
    ):
        if not bumped:
            no_progress_count += 1
        reason = "equivalent_patch"
    if not criterion_improved and current_findings and not bumped and reason is None:
        # no improvement signal
        pass
    return no_progress_count, reason


def should_terminate_no_progress(no_progress_count: int, threshold: int = 2) -> bool:
    return no_progress_count >= threshold
