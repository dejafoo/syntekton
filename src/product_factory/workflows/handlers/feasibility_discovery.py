"""Handler for the feasibility_discovery pack (PM1.D / WF1)."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.validation.pipeline import FEASIBILITY_REQUIRED_SECTIONS
from product_factory.workflows.artifacts import ROLE_FEASIBILITY_DOSSIER
from product_factory.workflows.default_plans import default_feasibility_discovery_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


class FeasibilityDiscoveryHandler:
    pack_id = "feasibility_discovery"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_feasibility_discovery_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_FEASIBILITY_DOSSIER:
            raise RuntimeError(f"feasibility_discovery does not compose role {role!r}")
        if not callable(ctx.compose_feasibility_dossier):
            raise RuntimeError("feasibility_discovery compose requires compose_feasibility_dossier")
        return str(
            ctx.compose_feasibility_dossier(
                ctx.request,
                findings=ctx.findings,
                dependency_outputs=ctx.dependency_outputs,
                document_name=ctx.document_name,
            )
        )

    def required_sections(self, role: str) -> tuple[str, ...]:
        return tuple(FEASIBILITY_REQUIRED_SECTIONS)

    def validator_id(self, role: str) -> str:
        return "feasibility_sections"

    def authority_class(self) -> AuthorityClass:
        return "read_only"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="change_intake",
                reason="A grounded dossier commonly feeds change intake framing",
            ),
            EligibleNextAction(
                pack_id="technical_plan",
                reason="A grounded dossier may also feed a technical plan directly",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
