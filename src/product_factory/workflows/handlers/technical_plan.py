"""Handler for the technical_plan pack (alias: architecture)."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_ARCHITECTURE_DOCUMENT
from product_factory.workflows.default_plans import default_technical_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)
from product_factory.validation.pipeline import ARCHITECTURE_REQUIRED_SECTIONS


class TechnicalPlanHandler:
    pack_id = "technical_plan"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_technical_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_ARCHITECTURE_DOCUMENT:
            raise RuntimeError(f"technical_plan does not compose role {role!r}")
        if ctx.generate_architecture is not None and not ctx.use_mock:
            text, _usage = ctx.generate_architecture()
            return str(text)
        if not callable(ctx.compose_architecture):
            raise RuntimeError("technical_plan compose requires compose_architecture")
        return str(
            ctx.compose_architecture(
                ctx.request.request_text,
                ctx.findings,
                document_name=ctx.document_name,
            )
        )

    def required_sections(self, role: str) -> tuple[str, ...]:
        return tuple(ARCHITECTURE_REQUIRED_SECTIONS)

    def validator_id(self, role: str) -> str:
        return "architecture_sections"

    def authority_class(self) -> AuthorityClass:
        return "external_read"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="repository_change",
                reason="Approved technical plans commonly precede repository changes",
            ),
            EligibleNextAction(
                pack_id="feasibility_discovery",
                reason="Unresolved unknowns may need a discovery pass before planning further",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
