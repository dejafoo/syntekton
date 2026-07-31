"""Handler for the repository_investigation pack."""

from __future__ import annotations

from datetime import UTC, datetime

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
        base = str(
            ctx.compose_evidence_report(
                ctx.request.request_text,
                findings=ctx.findings,
                dependency_outputs=ctx.dependency_outputs,
                document_name=ctx.document_name,
            )
        )
        pins = [
            ref
            for ref in ctx.request.handoff_refs
            if ref.schema_id in {"change_brief.v1", "feasibility_dossier.v1"}
        ]
        pin_lines = [
            (
                f"- {ref.role}: schema `{ref.schema_id}`; digest `{ref.digest}`; "
                f"producer `{ref.producer_run_id}/{ref.producer_task_id}`; state `{ref.state}`"
            )
            for ref in pins
        ] or ["- Unknown: no compatible pinned handoff was supplied."]
        pack_input = ctx.pack_input
        metadata = ctx.request.metadata
        revision = str(
            pack_input.get("repository_revision")
            or metadata.get("repository_revision")
            or metadata.get("base_commit")
            or "unknown"
        )
        started = str(
            pack_input.get("retrieval_started_at")
            or metadata.get("retrieval_started_at")
            or "unknown"
        )
        ended = str(
            pack_input.get("retrieval_ended_at")
            or metadata.get("retrieval_ended_at")
            or datetime.now(UTC).isoformat()
        )
        cited = "README.md"
        for line in base.splitlines():
            if line.strip().startswith("- `") and line.strip().endswith("`"):
                candidate = line.strip()[3:-1]
                if "/" in candidate or "." in candidate.lstrip("."):
                    cited = candidate
                    break
        v2_sections = [
            "## Repository snapshot",
            f"- Revision: `{revision}`",
            f"- Retrieval window: `{started}` to `{ended}`",
            "",
            "## Handoff pins",
            *pin_lines,
            "",
            "## Evidence",
            f"- Fact: Repository evidence was read from `{cited}`.",
            (
                "- Inference: The cited repository structure is relevant to the "
                "pinned change scope; confirm against implementation behavior."
            ),
            (
                "- Unknown: Connector freshness and any unavailable files remain "
                "unknown; no missing value was inferred."
            ),
            "",
        ]
        marker = "## Findings"
        if marker in base:
            base = base.replace(marker, "\n".join(v2_sections) + marker, 1)
        else:
            base += "\n" + "\n".join(v2_sections)
        if "## Unknowns" not in base:
            base += (
                "\n## Unknowns\n"
                "- Connector-unavailable or stale evidence remains unknown.\n"
                "- Product decisions not established by the pinned ChangeBrief require approval.\n"
            )
        return base

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
