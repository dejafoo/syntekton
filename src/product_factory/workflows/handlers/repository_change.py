"""Handler for the repository_change pack (alias: code_change)."""

from __future__ import annotations

import hashlib
import json
import re

from product_factory.domain.plans import PlannerOutput
from product_factory.workflows.artifacts import ROLE_CHANGE_SET, ROLE_PROPOSED_PATCH
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
        if role == ROLE_PROPOSED_PATCH:
            compose_fn = getattr(ctx, "compose_patch", None)
            if callable(compose_fn):
                return str(compose_fn(ctx))
            return ""
        if role != ROLE_CHANGE_SET:
            raise RuntimeError(f"repository_change does not compose role {role!r}")

        patch = ""
        for output in ctx.dependency_outputs:
            for excerpt in output.get("artifact_excerpts") or []:
                if (
                    excerpt.get("logical_name") == "proposed.patch"
                    or str(excerpt.get("logical_name") or "").endswith(".patch")
                ):
                    patch = str(excerpt.get("content") or "")
        if not patch:
            raise RuntimeError("ChangeSet composition requires the proposed patch")

        changed_paths: set[str] = set()
        for line in patch.splitlines():
            match = re.match(r"^\+\+\+\s+(?:b/)?(.+)$", line)
            if match and match.group(1) != "/dev/null":
                changed_paths.add(match.group(1))
            deleted = re.match(r"^---\s+(?:a/)?(.+)$", line)
            if deleted and deleted.group(1) != "/dev/null":
                changed_paths.add(deleted.group(1))

        acceptance_refs = [
            str(item)
            for item in (ctx.pack_input.get("acceptance_refs") or [])
            if str(item).strip()
        ]
        if not acceptance_refs:
            acceptance_refs = [
                f"{ref.schema_id}:{ref.digest}"
                for ref in ctx.request.handoff_refs
                if ref.schema_id.startswith("technical_plan.") or ref.schema_id == "change_brief.v1"
            ]
        evidence_refs = [
            str(item)
            for item in (ctx.pack_input.get("validation_evidence_refs") or [])
            if str(item).strip()
        ]
        evidence_refs.extend(ctx.validation_evidence_refs)
        evidence_refs = list(dict.fromkeys(evidence_refs))
        patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        payload = {
            "base_revision": ctx.base_revision,
            "patch_sha256": patch_digest,
            "artifact_hashes": {"proposed.patch": patch_digest},
            "changed_paths": sorted(changed_paths),
            "acceptance_refs": acceptance_refs,
            "validation_evidence_refs": evidence_refs,
            "producer_run_id": ctx.run_id,
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def required_sections(self, role: str) -> tuple[str, ...]:
        return ()

    def validator_id(self, role: str) -> str:
        if role == ROLE_CHANGE_SET:
            return "change_set_contract"
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
