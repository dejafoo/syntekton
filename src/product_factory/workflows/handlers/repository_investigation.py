"""Handler for the repository_investigation pack."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.validation.pipeline import INVESTIGATION_REQUIRED_SECTIONS
from product_factory.workflows.artifacts import ROLE_EVIDENCE_REPORT
from product_factory.workflows.default_plans import default_investigation_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


class RepositoryInvestigationHandler:
    pack_id = "repository_investigation"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_investigation_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_EVIDENCE_REPORT:
            raise RuntimeError(f"repository_investigation does not compose role {role!r}")
        if not callable(ctx.compose_evidence_report):
            raise RuntimeError("repository_investigation compose requires compose_evidence_report")
        return str(
            ctx.compose_evidence_report(
                ctx.request.request_text,
                findings=ctx.findings,
                dependency_outputs=ctx.dependency_outputs,
                document_name=ctx.document_name,
            )
        )

    def required_sections(self, role: str) -> tuple[str, ...]:
        return tuple(INVESTIGATION_REQUIRED_SECTIONS)

    def validator_id(self, role: str) -> str:
        return "investigation_sections"

    def authority_class(self) -> AuthorityClass:
        return "read_only"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="technical_plan",
                reason="Evidence reports commonly feed a technical plan",
            ),
            EligibleNextAction(
                pack_id="feasibility_discovery",
                reason="Open questions may warrant bounded public-evidence discovery",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
