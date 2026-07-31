"""Handler for the change_intake pack (PM2.A / WF2)."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST
from product_factory.workflows.change_intake import (
    CHANGE_BRIEF_REQUIRED_SECTIONS,
    CHANGE_INTAKE_VALIDATOR_IDS,
    CLARIFICATION_REQUIRED_SECTIONS,
)
from product_factory.workflows.default_plans import default_change_intake_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


class ChangeIntakeHandler:
    pack_id = "change_intake"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_change_intake_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role not in {ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST}:
            raise RuntimeError(f"change_intake does not compose role {role!r}")
        if not callable(ctx.compose_change_intake):
            raise RuntimeError("change_intake compose requires compose_change_intake")
        return str(
            ctx.compose_change_intake(
                ctx.request,
                role=role,
                findings=ctx.findings,
                dependency_outputs=ctx.dependency_outputs,
                document_name=ctx.document_name,
            )
        )

    def required_sections(self, role: str) -> tuple[str, ...]:
        if role == ROLE_CLARIFICATION_REQUEST:
            return tuple(CLARIFICATION_REQUIRED_SECTIONS)
        return tuple(CHANGE_BRIEF_REQUIRED_SECTIONS)

    def validator_id(self, role: str) -> str:
        return CHANGE_INTAKE_VALIDATOR_IDS.get(role, "intake_sections")

    def authority_class(self) -> AuthorityClass:
        return "read_only"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        # Brief path: investigation and/or technical plan. Clarification outcomes
        # must not auto-start work — hosts should surface questions to a human.
        return [
            EligibleNextAction(
                pack_id="repository_investigation",
                reason=(
                    "A pinned change brief commonly feeds a repository investigation "
                    "(skip when the primary landable was a clarification request)"
                ),
            ),
            EligibleNextAction(
                pack_id="technical_plan",
                reason=(
                    "A pinned change brief commonly feeds a technical plan "
                    "(skip when the primary landable was a clarification request)"
                ),
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
