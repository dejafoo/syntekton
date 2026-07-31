"""Handler for the technical_plan pack (alias: architecture)."""

from __future__ import annotations

import re

from product_factory.domain.plans import PlannerOutput
from product_factory.validation.pipeline import TECHNICAL_PLAN_REQUIRED_SECTIONS
from product_factory.workflows.artifacts import ROLE_ARCHITECTURE_DOCUMENT
from product_factory.workflows.default_plans import default_technical_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)


def _replace_section(markdown: str, heading: str, body: list[str]) -> str:
    pattern = re.compile(rf"(?ms)^##\s+{re.escape(heading)}\s*$.*?(?=^##\s+|\Z)")
    replacement = f"## {heading}\n" + "\n".join(body).rstrip() + "\n\n"
    if pattern.search(markdown):
        return pattern.sub(replacement, markdown, count=1)
    return markdown.rstrip() + "\n\n" + replacement


def _section_bullets(markdown: str, heading: str) -> list[str]:
    pattern = re.compile(rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)")
    match = pattern.search(markdown)
    if match is None:
        return []
    return [
        line.strip()[1:].strip()
        for line in match.group(1).splitlines()
        if line.strip().startswith(("-", "*"))
    ]


def _ensure_v2_contract(markdown: str, ctx: ComposeContext) -> str:
    acceptance = _section_bullets(markdown, "Acceptance criteria")
    if not acceptance:
        acceptance = ["Deliver the outcome defined by the pinned ChangeBrief."]
    acceptance = [
        re.sub(r"(?i)^AC[-_ ]?[0-9]+\s*[:.-]\s*", "", item).strip() for item in acceptance
    ]
    acceptance_lines = [f"- AC-{idx:03d}: {item}" for idx, item in enumerate(acceptance, 1)]

    questions = _section_bullets(markdown, "Open questions")
    normalized_questions = [
        re.sub(r"(?i)^DEC[-_ ]?[0-9]+\s*[:.-]\s*", "", item).strip()
        for item in questions
        if item.strip().lower() not in {"none", "none.", "n/a"}
    ]
    question_lines = [
        f"- DEC-{idx:03d}: {item}" for idx, item in enumerate(normalized_questions, 1)
    ] or ["No unresolved product decisions identified from available pinned inputs."]
    approval_lines = [
        f"- DEC-{idx:03d}: Approval required before selecting a value for: {item}"
        for idx, item in enumerate(normalized_questions, 1)
    ] or ["- None; revisit if evidence or scope changes."]

    pins = [
        ref
        for ref in ctx.request.handoff_refs
        if ref.schema_id
        in {
            "change_brief.v1",
            "evidence_report.document.v1",
            "evidence_report.document.v2",
            "feasibility_dossier.v1",
        }
    ]
    pin_lines = [
        (
            f"- {ref.role}: `{ref.schema_id}` digest `{ref.digest}` "
            f"from `{ref.producer_run_id}/{ref.producer_task_id}` ({ref.state})"
        )
        for ref in pins
    ] or ["- No compatible handoff pin supplied; plan remains a provisional draft."]

    markdown = _replace_section(markdown, "Acceptance criteria", acceptance_lines)
    markdown = _replace_section(markdown, "Open questions", question_lines)
    markdown = _replace_section(
        markdown,
        "Implementation slices",
        [
            (
                f"- SLICE-{idx:03d} → AC-{idx:03d}: implement the bounded behavior "
                "and preserve the cited constraints."
            )
            for idx in range(1, len(acceptance_lines) + 1)
        ],
    )
    markdown = _replace_section(
        markdown,
        "Verification evidence",
        [
            (
                f"- AC-{idx:03d}: capture a deterministic test result or reviewable "
                "artifact proving the criterion."
            )
            for idx in range(1, len(acceptance_lines) + 1)
        ],
    )
    markdown = _replace_section(markdown, "Approval items", approval_lines)
    return _replace_section(markdown, "Handoff pins", pin_lines)


class TechnicalPlanHandler:
    pack_id = "technical_plan"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_technical_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_ARCHITECTURE_DOCUMENT:
            raise RuntimeError(f"technical_plan does not compose role {role!r}")
        if ctx.generate_architecture is not None and not ctx.use_mock:
            text, _usage = ctx.generate_architecture()
            return _ensure_v2_contract(str(text), ctx)
        if not callable(ctx.compose_architecture):
            raise RuntimeError("technical_plan compose requires compose_architecture")
        return _ensure_v2_contract(
            str(
                ctx.compose_architecture(
                    ctx.request.request_text,
                    ctx.findings,
                    document_name=ctx.document_name,
                )
            ),
            ctx,
        )

    def required_sections(self, role: str) -> tuple[str, ...]:
        return tuple(TECHNICAL_PLAN_REQUIRED_SECTIONS)

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
