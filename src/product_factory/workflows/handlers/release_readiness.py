"""Registered runtime behavior for the monitor-only release-readiness pack."""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_RELEASE_PLAN
from product_factory.workflows.default_plans import default_release_readiness_plan
from product_factory.workflows.handlers.base import (
    AuthorityClass,
    ComposeContext,
    EligibleNextAction,
)

ReleaseReadinessOutcome = Literal["ready", "blocked", "needs_decision"]


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(item).strip() for item in value.values() if str(item).strip()]
    if not isinstance(value, (list, tuple, set)):
        return [str(value).strip()] if str(value or "").strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _objects(value: object, *, key: str) -> list[dict[str, object]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) if isinstance(item, dict) else {key: str(item)} for item in value]


class ReleaseReadinessHandler:
    pack_id = "release_readiness"

    def plan_template(self, request_text: str) -> PlannerOutput:
        return default_release_readiness_plan(request_text)

    def compose(self, role: str, ctx: ComposeContext) -> str:
        if role != ROLE_RELEASE_PLAN:
            raise RuntimeError(f"release_readiness does not compose role {role!r}")
        data = ctx.pack_input
        input_digests = {
            str(key): str(value).lower()
            for key, value in dict(data.get("input_digests") or {}).items()
            if str(key).strip() and str(value).strip()
        }
        for ref in ctx.request.handoff_refs:
            digest = str(ref.digest or "").lower()
            if len(digest) == 64:
                input_digests.setdefault(ref.role or ref.schema_id, digest)
        commit_sha = str(data.get("commit_sha") or "").strip().lower()
        if commit_sha:
            input_digests.setdefault(
                "commit",
                hashlib.sha256(commit_sha.encode("utf-8")).hexdigest(),
            )

        verification = _strings(
            data.get("verification_evidence")
            or data.get("verification_evidence_refs")
            or data.get("evidence_refs")
            or ctx.validation_evidence_refs
        )
        migration = _strings(data.get("migration_preconditions") or data.get("migration_evidence"))
        rollback = _objects(
            data.get("rollback_criteria") or data.get("rollback_evidence"),
            key="criterion",
        )
        decisions = _strings(data.get("unresolved_decisions") or data.get("decision_items"))
        # SD1.E: ready requires analysis-task receipts, not caller pack_input alone.
        analysis_receipts = 0
        for dependency in ctx.dependency_outputs or []:
            for ref in dependency.get("artifact_refs", []) or []:
                name = str(ref.get("logical_name") or "")
                if "release-analysis" in name or "operations-analysis" in name:
                    analysis_receipts += 1
        missing: list[str] = []
        if not input_digests:
            missing.append("input_digests")
        if not verification:
            missing.append("verification_evidence")
        if not migration:
            missing.append("migration_preconditions")
        if not rollback:
            missing.append("rollback_criteria")
        if analysis_receipts < 1:
            missing.append("analysis_task_receipts")
        outcome: ReleaseReadinessOutcome
        if missing:
            outcome = "blocked"
        elif decisions:
            outcome = "needs_decision"
        else:
            outcome = "ready"

        digest_refs = sorted(input_digests)
        claims = [
            {
                "claim": "verification evidence is present",
                "input_digest_refs": digest_refs,
                "evidence_refs": verification,
            },
            {
                "claim": "migration preconditions are explicit",
                "input_digest_refs": digest_refs,
                "evidence_refs": migration,
            },
            {
                "claim": "rollback criteria are explicit",
                "input_digest_refs": digest_refs,
                "evidence_refs": [
                    str(item.get("evidence_ref") or item.get("criterion") or "")
                    for item in rollback
                ],
            },
        ]
        payload = {
            "schema_id": "release_plan.v1",
            "outcome": outcome,
            "input_digests": input_digests,
            "version": str(data.get("version") or commit_sha[:12] or "unknown"),
            "change_notes": _strings(data.get("change_notes") or [ctx.request.request_text]),
            "compatibility_impact": _strings(
                data.get("compatibility_impact") or ["unknown unless supplied"]
            ),
            "verification_evidence": verification,
            "migration_preconditions": migration,
            "rollout_phases": _objects(
                data.get("rollout_phases")
                or [{"name": "monitor-only assessment", "effect": "none"}],
                key="name",
            ),
            "monitors": _objects(
                data.get("monitors") or [{"name": "declared release health signals"}],
                key="name",
            ),
            "rollback_criteria": rollback,
            "required_approvals": _strings(data.get("required_approvals")),
            "unresolved_decisions": decisions,
            "missing_evidence": missing,
            "claims": claims,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ()

    def validator_id(self, role: str) -> str:
        return "release_plan_contract"

    def authority_class(self) -> AuthorityClass:
        return "external_read"

    def eligible_next_actions(self) -> list[EligibleNextAction]:
        # Deployment is intentionally absent until PM5.B.
        return [
            EligibleNextAction(
                pack_id="change_intake",
                reason="Release blockers or decisions can be reframed as a bounded change",
            ),
            EligibleNextAction(
                pack_id="repository_investigation",
                reason="Missing release evidence can be investigated read-only",
            ),
        ]

    def findings_are_deliverable(self) -> bool:
        return True


__all__ = ["ReleaseReadinessHandler", "ReleaseReadinessOutcome"]
