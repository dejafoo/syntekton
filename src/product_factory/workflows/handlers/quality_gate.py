"""Handler for the quality_gate pack."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import (
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
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
        }.get(role, f"{role}.md")
