"""Human-review and authority composition gates (PM5.C / G4).

Composition gates keep method skills, domain reference packs, and policy
profiles separated from tool grants. Skill or pack selection cannot widen
mutation authority; conflicts fail closed as ``composition_conflict``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from product_factory.domain.errors import UnsafeOperationError
from product_factory.policy.domain_packs import DomainReferencePack
from product_factory.policy.policy_profiles import CompositionPolicyProfile

MUTATION_TOOL_CLASSES = frozenset(
    {
        "repository_write",
        "git_write",
        "deployment_read",
        "deployment_write",
    }
)
MUTATION_TOOL_NAMES = frozenset(
    {
        "create_file",
        "apply_patch",
        "resolve_deployment_target",
        "start_deployment",
        "get_rollout_status",
        "verify_health",
        "rollback_deployment",
    }
)
READ_ONLY_WORKFLOWS = frozenset(
    {
        "feasibility_discovery",
        "change_intake",
        "repository_investigation",
        "release_readiness",
        "incident_triage",
        "service_health_review",
        "quality_gate",
    }
)


class CompositionConflictError(UnsafeOperationError):
    """Raised when composed profiles/packs conflict or widen authority."""

    exit_code = 8


@dataclass(frozen=True)
class CompositionGateResult:
    ok: bool
    requires_human_review: bool = False
    recommendation: str | None = None
    reference_pack_ids: list[str] = field(default_factory=list)
    policy_profile_ids: list[str] = field(default_factory=list)
    profile_digests: dict[str, str] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.conflicts:
            return "composition_conflict"
        if self.requires_human_review:
            return "needs_expert_review"
        return "ok"


def _topics_from_request(request: Any) -> set[str]:
    pack_input = getattr(request, "pack_input", None) or {}
    chunks = [
        str(pack_input.get("domain") or ""),
        str(pack_input.get("decision_statement") or ""),
        str(getattr(request, "request_text", "") or ""),
        " ".join(str(x) for x in (pack_input.get("actors") or [])),
    ]
    text = " ".join(chunks).lower()
    topics = {
        topic
        for topic in (
            "clinical",
            "legal",
            "privacy",
            "compliance",
            "production_target",
            "regulated_change",
        )
        if topic in text
    }
    domain = str(pack_input.get("domain") or "").lower()
    if any(token in domain for token in ("clinical", "health", "fhir", "ehr")):
        topics.add("clinical")
    if pack_input.get("require_expert_review"):
        topics.add("clinical")
    return topics


def assert_no_authority_widening(
    *,
    workflow_type: str,
    granted_tool_names: Iterable[str],
    granted_tool_classes: Iterable[str],
    domain_packs: Iterable[DomainReferencePack],
    policy_profiles: Iterable[CompositionPolicyProfile],
    skill_ids: Iterable[str] = (),
) -> None:
    """Fail closed when composition would widen data/mutation authority."""

    packs = list(domain_packs)
    profiles = list(policy_profiles)
    tools = set(granted_tool_names)
    classes = set(granted_tool_classes)
    conflicts: list[dict[str, Any]] = []

    for pack in packs:
        if pack.grants.additional_tool_classes or pack.grants.additional_authority:
            conflicts.append(
                {
                    "kind": "domain_pack_authority",
                    "pack_id": pack.id,
                    "additional_tool_classes": list(pack.grants.additional_tool_classes),
                    "additional_authority": list(pack.grants.additional_authority),
                }
            )
        if (
            pack.permitted_workflows
            and workflow_type
            and workflow_type not in pack.permitted_workflows
        ):
            conflicts.append(
                {
                    "kind": "domain_pack_workflow",
                    "pack_id": pack.id,
                    "workflow_type": workflow_type,
                    "permitted_workflows": list(pack.permitted_workflows),
                }
            )

    widening_profiles = [
        profile
        for profile in profiles
        if profile.deny_authority_widening or profile.deny_additional_tool_classes
    ]
    if workflow_type in READ_ONLY_WORKFLOWS and widening_profiles:
        extra_tools = sorted(tools & MUTATION_TOOL_NAMES)
        extra_classes = sorted(classes & MUTATION_TOOL_CLASSES)
        if extra_tools or extra_classes:
            conflicts.append(
                {
                    "kind": "authority_widening",
                    "workflow_type": workflow_type,
                    "extra_tool_names": extra_tools,
                    "extra_tool_classes": extra_classes,
                    "skill_ids": list(skill_ids),
                    "policy_profile_ids": [p.id for p in widening_profiles],
                }
            )

    if "deployment.change-control" in set(skill_ids) and workflow_type in READ_ONLY_WORKFLOWS:
        conflicts.append(
            {
                "kind": "skill_authority_smuggle",
                "skill_id": "deployment.change-control",
                "workflow_type": workflow_type,
            }
        )

    for profile in profiles:
        if (
            profile.permitted_workflows
            and workflow_type
            and workflow_type not in profile.permitted_workflows
        ):
            conflicts.append(
                {
                    "kind": "policy_workflow",
                    "profile_id": profile.id,
                    "workflow_type": workflow_type,
                    "permitted_workflows": list(profile.permitted_workflows),
                }
            )

    if conflicts:
        raise CompositionConflictError(
            "composition_conflict",
            details={"conflicts": conflicts},
        )


def evaluate_composition_gates(
    *,
    request: Any,
    domain_packs: Iterable[DomainReferencePack],
    policy_profiles: Iterable[CompositionPolicyProfile],
    granted_tool_names: Iterable[str] = (),
    granted_tool_classes: Iterable[str] = (),
    skill_ids: Iterable[str] = (),
) -> CompositionGateResult:
    """Evaluate human-review and authority gates for a composed run."""

    packs = list(domain_packs)
    profiles = list(policy_profiles)
    digests: dict[str, str] = {}
    for pack in packs:
        digests.update(pack.as_manifest_entry())
    for profile in profiles:
        digests.update(profile.as_manifest_entry())

    workflow_type = str(getattr(request, "workflow_type", "") or "")
    try:
        assert_no_authority_widening(
            workflow_type=workflow_type,
            granted_tool_names=granted_tool_names,
            granted_tool_classes=granted_tool_classes,
            domain_packs=packs,
            policy_profiles=profiles,
            skill_ids=skill_ids,
        )
    except CompositionConflictError as exc:
        return CompositionGateResult(
            ok=False,
            reference_pack_ids=[p.id for p in packs],
            policy_profile_ids=[p.id for p in profiles],
            profile_digests=digests,
            conflicts=list((exc.details or {}).get("conflicts") or []),
        )

    topics = _topics_from_request(request)
    review_topics = {
        topic
        for profile in profiles
        for topic in profile.require_human_review_for
        if profile.requires_human_review(topic) and topic in topics
    }
    for pack in packs:
        if pack.required_review and (
            "clinical" in topics
            or "compliance" in topics
            or pack.domain.lower() in {"health-interoperability", "clinical"}
        ):
            review_topics.add(pack.required_review)

    requires_review = bool(review_topics) or any(pack.required_review for pack in packs if topics)
    # Domain reference packs on regulated discovery always escalate.
    if packs and workflow_type == "feasibility_discovery" and profiles:
        if any(p.id == "regulated-data" for p in profiles) or topics:
            requires_review = True

    return CompositionGateResult(
        ok=True,
        requires_human_review=requires_review,
        recommendation="needs_expert_review" if requires_review else None,
        reference_pack_ids=[p.id for p in packs],
        policy_profile_ids=[p.id for p in profiles],
        profile_digests=digests,
    )


__all__ = [
    "CompositionConflictError",
    "CompositionGateResult",
    "MUTATION_TOOL_CLASSES",
    "MUTATION_TOOL_NAMES",
    "assert_no_authority_widening",
    "evaluate_composition_gates",
]
