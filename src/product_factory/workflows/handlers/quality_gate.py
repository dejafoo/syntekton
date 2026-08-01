"""Handler for the quality_gate pack."""

from __future__ import annotations

import json

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import (
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
    ROLE_VERIFICATION_REPORT,
)
from product_factory.workflows.default_plans import default_quality_gate_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)
from product_factory.workflows.quality_gate import (
    QUALITY_GATE_REQUIRED_SECTIONS,
    QUALITY_GATE_VALIDATOR_IDS,
)


class QualityGateHandler:
    pack_id = "quality_gate"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_quality_gate_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role == ROLE_VERIFICATION_REPORT:
            return self._compose_verification_report(ctx)
        if not callable(ctx.compose_quality_document):
            raise RuntimeError(f"quality_gate compose requires compose_quality_document for {role}")
        return str(
            ctx.compose_quality_document(
                role=role,
                request=ctx.request,
                dependency_outputs=ctx.dependency_outputs,
                document_name=ctx.document_name,
            )
        )

    @staticmethod
    def _compose_verification_report(ctx: ComposeContext) -> str:
        change_set = ctx.pack_input.get("change_set") or {}
        acceptance_refs = [
            str(value)
            for value in (
                ctx.pack_input.get("acceptance_refs")
                or change_set.get("acceptance_refs")
                or []
            )
            if str(value).strip()
        ]
        if not acceptance_refs:
            acceptance_refs = [
                f"{ref.schema_id}:{ref.digest}"
                for ref in ctx.request.handoff_refs
                if ref.schema_id.startswith("technical_plan.")
            ]

        evidence_refs = [
            str(value)
            for value in (
                ctx.pack_input.get("evidence_refs")
                or change_set.get("validation_evidence_refs")
                or []
            )
            if str(value).strip()
        ]
        evidence_refs.extend(ctx.validation_evidence_refs)
        evidence_refs.extend(
            f"{ref.schema_id}:{ref.digest}"
            for ref in ctx.request.handoff_refs
            if ref.schema_id == "validation_evidence.v1"
        )
        evidence_refs = list(dict.fromkeys(evidence_refs))

        dependency_findings = [
            finding
            for output in ctx.dependency_outputs
            for finding in (output.get("findings") or [])
        ]
        blocking = [
            finding
            for finding in dependency_findings
            if finding.get("severity") == "blocking" and finding.get("status", "open") == "open"
        ]
        if not acceptance_refs:
            acceptance_refs = list(
                dict.fromkeys(
                    str(finding.get("criterion_id"))
                    for finding in blocking
                    if finding.get("criterion_id")
                )
            )
        validator_results = [
            *list(ctx.pack_input.get("validator_results") or []),
            *ctx.validator_results,
        ]
        skipped = [
            result
            for result in validator_results
            if str(result.get("status") or "").lower() in {"skipped", "not_run"}
        ]
        failed = [
            result
            for result in validator_results
            if str(result.get("status") or "").lower() == "fail"
        ]

        supplied_results = ctx.pack_input.get("acceptance_results") or []
        if supplied_results:
            acceptance_results = [dict(item) for item in supplied_results]
        else:
            status = "fail" if blocking or failed else ("pass" if evidence_refs and not skipped else "gap")
            acceptance_results = [
                {
                    "acceptance_ref": acceptance_ref,
                    "status": status,
                    "evidence_refs": evidence_refs if status != "gap" else [],
                }
                for acceptance_ref in acceptance_refs
            ]

        statuses = {str(item.get("status") or "") for item in acceptance_results}
        residual_risk = [
            str(value)
            for value in (ctx.pack_input.get("residual_risk") or [])
            if str(value).strip()
        ]
        if blocking or failed or "fail" in statuses:
            outcome = "blocked"
        elif skipped or "gap" in statuses or not acceptance_results:
            outcome = "insufficient_evidence"
        elif residual_risk:
            outcome = "passes_with_risk"
        else:
            outcome = "passes"

        payload = {
            "outcome": outcome,
            "acceptance_results": acceptance_results,
            "validator_profile_id": str(
                ctx.pack_input.get("validator_profile_id") or ctx.profile or "quality_gate.v2"
            ),
            "evidence_refs": evidence_refs,
            "residual_risk": residual_risk,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return QUALITY_GATE_REQUIRED_SECTIONS.get(role, ())

    def validator_id(self, role: str) -> str:
        return QUALITY_GATE_VALIDATOR_IDS.get(role, f"{role}_sections")

    def authority_class(self) -> AuthorityClass:
        return "read_only"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="repository_change",
                reason="Quality findings can inform a bounded repository change",
            ),
            EligibleNextAction(
                pack_id="technical_plan",
                reason="Findings may feed a technical plan revision",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return True

    def role_default_name(self, role: str) -> str:
        return {
            ROLE_TEST_PLAN: "TEST_PLAN.md",
            ROLE_QUALITY_FINDINGS: "QUALITY_FINDINGS.md",
            ROLE_SECURITY_EVIDENCE: "SECURITY_EVIDENCE.md",
            ROLE_VERIFICATION_REPORT: "verification-report.json",
        }.get(role, f"{role}.md")
