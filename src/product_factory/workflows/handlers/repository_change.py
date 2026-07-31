"""Handler for the repository_change pack (alias: code_change)."""

from __future__ import annotations

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_PROPOSED_PATCH
from product_factory.workflows.default_plans import default_code_change_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


class RepositoryChangeHandler:
    pack_id = "repository_change"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_code_change_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_PROPOSED_PATCH:
            raise RuntimeError(f"repository_change does not compose role {role!r}")
        compose_fn = getattr(ctx, "compose_patch", None)
        if callable(compose_fn):
            return str(compose_fn(ctx))
        return ""

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ()

    def validator_id(self, role: str) -> str:
        return "patch_applies"

    def authority_class(self) -> AuthorityClass:
        return "isolated_write"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        return [
            EligibleNextAction(
                pack_id="quality_gate",
                reason="Proposed patches can be assessed by a quality gate",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return False
