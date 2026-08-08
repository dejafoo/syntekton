"""ValidationRepairService — validation pipelines and repair eligibility (SD2).

PackExecutionPolicy is the sole declaration for validators, repair eligibility,
and findings-as-deliverable behavior. Named workflow branches are not used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskSpec
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.orchestration.repair import create_repair_tasks
from product_factory.orchestration.review_findings import validate_review_findings
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.validation.pipeline import (
    validate_architecture_document,
    validate_architecture_request_specificity,
    validate_behavioral_commands,
    validate_citations,
    validate_investigation_document,
    validate_patch_applies,
    validate_path_scope,
    validate_secrets,
)
from product_factory.workflows.base import PackExecutionPolicy, WorkflowPack
from product_factory.workflows.registry import is_registered_workflow, resolve_workflow_pack


def changed_files_from_patch(patch: str) -> list[str]:
    files: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.append(line[6:])
    return files


def resolve_validation_command_ids(request: RunRequest) -> list[str]:
    """`RunRequest.validation_commands` is the source of truth (P1.C)."""
    if request.validation_commands:
        return list(request.validation_commands)
    return [
        value.strip()
        for value in str(request.metadata.get("smoke_commands", "")).split(",")
        if value.strip()
    ]


class ValidationRepairService:
    """Owns mid-wave validation and repair-task creation decisions."""

    def __init__(self, *, config: AppConfig, raw_gateway: Any) -> None:
        self.config = config
        self._raw_gateway = raw_gateway

    def pack_policy(self, request: RunRequest) -> PackExecutionPolicy | None:
        if not is_registered_workflow(request.workflow_type):
            return None
        return resolve_workflow_pack(request.workflow_type).execution_policy

    def validate_outputs(
        self,
        *,
        request: RunRequest,
        patch_text: str,
        architecture_md: str,
        original_repo: Path | None,
        task: TaskSpec,
        findings: list[Finding] | None = None,
        ledger: BudgetLedger | None = None,
        evidence_report_md: str = "",
        artifact_store: ArtifactStore | None = None,
        input_revision: str = "worktree",
        workflow_pack: WorkflowPack | None = None,
    ) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        policy = (
            workflow_pack.execution_policy
            if workflow_pack is not None
            else self.pack_policy(request)
        )
        validators = set(policy.validators) if policy is not None else set()
        output_roles = set(policy.output_roles) if policy is not None else set()

        # Patch pipeline: packs that declare patch_applies / proposed_patch roles.
        if (
            patch_text
            and original_repo
            and (
                "patch_applies" in validators
                or "proposed_patch" in output_roles
                or "path_scope" in validators
            )
        ):
            if "patch_applies" in validators or "proposed_patch" in output_roles:
                results.append(validate_patch_applies(original_repo, patch_text))
            changed = changed_files_from_patch(patch_text)
            if "path_scope" in validators or "proposed_patch" in output_roles:
                results.append(validate_path_scope(changed, task.allowed_path_patterns))
            if "secret_scan" in validators or "proposed_patch" in output_roles:
                results.append(validate_secrets(patch_text))
            command_ids = resolve_validation_command_ids(request)
            if task.expected_output_schema != "change_set.v1" and (
                "patch_applies" in validators or "proposed_patch" in output_roles
            ):
                baselines = request.pack_input.get("validation_baselines") or {}
                results.extend(
                    validate_behavioral_commands(
                        repository=original_repo,
                        patch=patch_text,
                        command_ids=command_ids,
                        registered_commands=self.config.policies.registered_commands,
                        ledger=ledger,
                        artifact_store=artifact_store,
                        created_by_task_id=task.id,
                        input_revision=input_revision,
                        validation_baselines=baselines if isinstance(baselines, dict) else {},
                    )
                )

        # Architecture pipeline: packs declaring architecture validators/roles.
        if architecture_md and (
            "architecture_sections" in validators
            or "architecture_document" in output_roles
            or "architecture_substance" in validators
        ):
            results.append(validate_architecture_document(architecture_md))
            must_cover = [
                item.strip()
                for item in str(request.metadata.get("must_cover") or "").split("|")
                if item.strip()
            ]
            if must_cover or not isinstance(self._raw_gateway, MockGateway):
                results.extend(
                    validate_architecture_request_specificity(
                        architecture_md,
                        must_cover=must_cover or None,
                        reject_boilerplate=not isinstance(self._raw_gateway, MockGateway),
                    )
                )
            results.append(validate_secrets(architecture_md))

        if "investigation_sections" in validators and evidence_report_md:
            results.append(validate_investigation_document(evidence_report_md))
            results.append(validate_citations(evidence_report_md))
            results.append(validate_secrets(evidence_report_md))

        if task.capability == "independent_review" and findings is not None:
            preliminary = validate_review_findings(findings)
            if preliminary.status == "fail":
                bad_ids = set(preliminary.details.get("finding_ids") or [])
                for finding in findings:
                    if finding.id in bad_ids and finding.severity == "blocking":
                        finding.severity = "major"
                        finding.confidence = min(finding.confidence, 0.49)
            results.append(validate_review_findings(findings))
        return results

    def create_repairs(
        self,
        *,
        request: RunRequest,
        failures: list[ValidatorResult],
        findings: list[Finding],
        originating_task_id: str,
        allowed_path_patterns: list[str],
        next_id_start: int,
    ) -> list[Any]:
        return create_repair_tasks(
            failures=failures,
            findings=findings,
            originating_task_id=originating_task_id,
            allowed_path_patterns=allowed_path_patterns,
            next_id_start=next_id_start,
            registered_command_ids=resolve_validation_command_ids(request)
            or list(self.config.policies.registered_commands),
        )

    def is_repair_eligible(
        self,
        *,
        capability: str,
        workflow_pack: WorkflowPack | None,
    ) -> bool:
        if workflow_pack is None:
            return True
        return capability in workflow_pack.execution_policy.repair_eligible_capabilities
