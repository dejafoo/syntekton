"""RunFinalizer — terminal outputs, artifact roles, and final status (SD2)."""

from __future__ import annotations

from dataclasses import dataclass

from product_factory.domain.findings import ValidatorResult
from product_factory.domain.runs import FinalStatus, RunRequest
from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ROLE_CHANGE_SET,
    ROLE_PROPOSED_PATCH,
    ArtifactLandMap,
)
from product_factory.workflows.base import PackExecutionPolicy, WorkflowPack


@dataclass
class FinalizationDecision:
    final_status: FinalStatus
    notes: list[str]
    missing_policy_roles: list[str]
    requires_approval: bool


class RunFinalizer:
    """Owns final artifact-role enforcement and terminal status decisions."""

    def missing_required_roles(
        self,
        *,
        policy: PackExecutionPolicy,
        documents_by_role: dict[str, str],
        patch_text: str,
    ) -> list[str]:
        present = set(documents_by_role)
        if patch_text.strip():
            present.add(ROLE_PROPOSED_PATCH)
        return sorted(set(policy.required_output_roles) - present)

    def exclusive_group_violations(
        self,
        *,
        policy: PackExecutionPolicy,
        documents_by_role: dict[str, str],
    ) -> list[list[str]]:
        violations: list[list[str]] = []
        present = set(documents_by_role)
        for group in policy.exactly_one_output_role_groups:
            hits = sorted(group & present)
            if len(hits) != 1:
                violations.append(hits if hits else sorted(group))
        return violations

    def decide_status(
        self,
        *,
        request: RunRequest,
        workflow_pack: WorkflowPack | None,
        validation_results: list[ValidatorResult],
        patch_text: str,
        documents_by_role: dict[str, str],
        land_map: ArtifactLandMap,
        findings_count: int,
    ) -> FinalizationDecision:
        notes: list[str] = []
        policy = workflow_pack.execution_policy if workflow_pack is not None else None
        missing = (
            self.missing_required_roles(
                policy=policy,
                documents_by_role=documents_by_role,
                patch_text=patch_text,
            )
            if policy is not None
            else []
        )
        exclusive = (
            self.exclusive_group_violations(policy=policy, documents_by_role=documents_by_role)
            if policy is not None
            else []
        )
        blocking = any(v.status in {"fail", "error"} for v in validation_results)
        requires_patch = bool(policy is not None and ROLE_PROPOSED_PATCH in policy.output_roles)
        empty_patch = requires_patch and not patch_text.strip()
        requires_approval = bool(policy is not None and policy.approval_required)
        if missing or exclusive or blocking or empty_patch:
            notes.append("finalization blocked by validation or missing roles")
            return FinalizationDecision(
                final_status="failed",
                notes=notes,
                missing_policy_roles=missing,
                requires_approval=False,
            )
        if requires_approval:
            return FinalizationDecision(
                final_status="awaiting_approval",
                notes=notes,
                missing_policy_roles=[],
                requires_approval=True,
            )
        return FinalizationDecision(
            final_status="completed",
            notes=notes,
            missing_policy_roles=[],
            requires_approval=False,
        )

    def pack_declares_patch_output(self, workflow_pack: WorkflowPack | None) -> bool:
        if workflow_pack is None:
            return False
        return ROLE_PROPOSED_PATCH in workflow_pack.execution_policy.output_roles

    def pack_declares_architecture_output(self, workflow_pack: WorkflowPack | None) -> bool:
        if workflow_pack is None:
            return False
        roles = set(workflow_pack.execution_policy.output_roles)
        validators = set(workflow_pack.execution_policy.validators)
        return ROLE_ARCHITECTURE_DOCUMENT in roles or "architecture_sections" in validators

    def pack_declares_change_set(self, workflow_pack: WorkflowPack | None) -> bool:
        if workflow_pack is None:
            return False
        return ROLE_CHANGE_SET in workflow_pack.execution_policy.output_roles
