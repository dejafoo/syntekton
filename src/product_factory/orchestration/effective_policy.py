"""Effective task policy — one durable grant for prompt, broker, and resume."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.capabilities import CAPABILITY_TOOL_CLASSES
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskSpec
from product_factory.tools.registry import ToolRegistry

EFFECTIVE_TASK_POLICY_SCHEMA = "effective_task_policy.v2"
LEGACY_UNRESOLVED = "legacy_unresolved"

ExecutorMode = Literal[
    "deterministic",
    "model_draft",
    "repository_agent_loop",
    "research_agent_loop",
    "interface_agent_loop",
    "validation",
    "composition",
]


class EffectiveTaskPolicy(BaseModel):
    """Immutable policy snapshot resolved before context assembly (ADR-007)."""

    model_config = {"extra": "forbid"}

    schema_version: str = EFFECTIVE_TASK_POLICY_SCHEMA
    task_id: str
    run_id: str
    pack_id: str | None = None
    pack_version: str | None = None
    pack_policy_digest: str | None = None
    capability: str
    descriptor_version: str | None = None
    executor_mode: ExecutorMode = "model_draft"
    executor_adapter_id: str | None = None
    allowed_tool_names: list[str] = Field(default_factory=list)
    allowed_tool_classes: list[str] = Field(default_factory=list)
    connector_decisions: dict[str, str] = Field(default_factory=dict)
    allowed_connector_classes: list[str] = Field(default_factory=list)
    allowed_connector_names: list[str] = Field(default_factory=list)
    workspace_access: Literal["none", "read_only", "isolated_write"] = "none"
    path_scopes: dict[str, list[str]] = Field(default_factory=dict)
    call_limits: dict[str, int] = Field(default_factory=dict)
    result_limits: dict[str, int] = Field(default_factory=dict)
    data_classification: str = "mixed"
    prompt_tool_names: list[str] = Field(default_factory=list)
    prompt_reduction_reason: str | None = None
    skill_ids: list[str] = Field(default_factory=list)
    profile_ids: list[str] = Field(default_factory=list)
    reference_pack_ids: list[str] = Field(default_factory=list)
    stack_profile_artifact_sha256: str | None = None
    stack_profile_digest: str | None = None
    stack_profile_schema_version: str | None = None
    route_class: str = "cloud"
    primary_model_profile: str
    fallback_model_profile: str | None = None
    fallback_eligible: bool = False
    budget_ceiling: dict[str, Any] = Field(default_factory=dict)
    validator_ids: list[str] = Field(default_factory=list)
    repair_eligible: bool = False
    approval_required: bool = True
    external_action_requires_approval: bool = False
    policy_digest: str = ""

    def ensure_prompt_subset(self) -> None:
        allowed = set(self.allowed_tool_names)
        outside = [name for name in self.prompt_tool_names if name not in allowed]
        if outside:
            raise ValueError(
                f"prompt_tool_names must be a subset of allowed_tool_names; got extras {outside}"
            )

    def computed_digest(self) -> str:
        """Return the stable digest of this policy excluding its own digest."""
        payload = self.model_dump(mode="json", exclude={"policy_digest"})
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(body).hexdigest()

    def ensure_valid_digest(self) -> None:
        if not self.policy_digest or self.policy_digest != self.computed_digest():
            raise ValueError("effective task policy digest is missing or does not match")


def workspace_access_for_tool_classes(tool_classes: set[str] | frozenset[str]) -> str:
    """Derive workspace authority from the durable tool-class grant only."""
    if tool_classes & {"repository_write", "git_write", "synthetic_write"}:
        return "isolated_write"
    if tool_classes & {
        "repository_read",
        "git_read",
        "validation_command",
        "interface_analysis",
    }:
        return "read_only"
    return "none"


def compute_allowed_tool_names(
    *,
    task: TaskSpec,
    request: RunRequest,
    tool_registry: ToolRegistry,
    connector_tool_names: frozenset[str],
    grantable_connector_tools: frozenset[str],
    web_search_tool: str = "web_search",
    denied_tool_names: frozenset[str] = frozenset(),
    pack_allowed_tool_classes: frozenset[str] | None = None,
) -> tuple[set[str], dict[str, str], list[str]]:
    """Resolve the exact broker grant and connector decisions for a task.

    Authority intersection (no layer may widen the layer above):

    ``descriptor maximum ∩ capability pack grant ∩ task.required_tool_classes``
    then environment connector availability and pack ``denied_tool_names``.

    ``request`` / ``web_search_tool`` remain for call-site compatibility.
    """

    _ = (request, web_search_tool)
    descriptor_max = CAPABILITY_TOOL_CLASSES.get(task.capability, frozenset())
    if pack_allowed_tool_classes is not None:
        permitted_classes = frozenset(descriptor_max & pack_allowed_tool_classes)
    else:
        permitted_classes = frozenset(descriptor_max)

    requested = frozenset(task.required_tool_classes)
    # A task must explicitly ask for every authority it receives.  Legacy
    # aliases are deliberately not broadened here: they are rejected by plan
    # compilation before a task reaches this resolver.
    class_filter = permitted_classes & requested

    granted = {tool.name for tool in tool_registry.list() if tool.tool_class in class_filter}

    connector_decisions: dict[str, str] = {}
    if connector_tool_names:
        granted -= connector_tool_names
        for name in sorted(connector_tool_names):
            connector_decisions[name] = "deny:not_in_capability_or_disabled"
        granted |= set(grantable_connector_tools)
        for name in sorted(grantable_connector_tools):
            connector_decisions[name] = "allow"
    granted -= denied_tool_names

    classes = sorted(
        {t.tool_class for t in tool_registry.list() if t.name in granted}
        | (set(requested) & set(permitted_classes))
    )
    return granted, connector_decisions, classes


def resolve_effective_task_policy(
    *,
    run_id: str,
    task: TaskSpec,
    request: RunRequest,
    tool_registry: ToolRegistry,
    model_profile: str,
    agent_profile: str,
    skill_ids: list[str],
    pack_id: str | None = None,
    pack_version: str | None = None,
    pack_policy_digest: str | None = None,
    descriptor_version: str | None = None,
    executor_adapter_id: str | None = None,
    connector_tool_names: frozenset[str] = frozenset(),
    grantable_connector_tools: frozenset[str] = frozenset(),
    web_search_tool: str = "web_search",
    stack_profile_digest: str | None = None,
    stack_profile_artifact_sha256: str | None = None,
    stack_profile_schema_version: str | None = None,
    reference_pack_ids: list[str] | None = None,
    profile_ids: list[str] | None = None,
    route_class: str = "cloud",
    fallback_model_profile: str | None = None,
    fallback_eligible: bool = False,
    validator_ids: list[str] | None = None,
    prompt_tool_names: list[str] | None = None,
    prompt_reduction_reason: str | None = None,
    executor_mode: ExecutorMode = "model_draft",
    denied_tool_names: frozenset[str] = frozenset(),
    pack_allowed_tool_classes: frozenset[str] | None = None,
    repair_eligible: bool | None = None,
    approval_required: bool | None = None,
    external_action_requires_approval: bool | None = None,
    allowed_connector_classes: frozenset[str] = frozenset(),
) -> EffectiveTaskPolicy:
    """Build the durable policy object enforced by broker and prompt builders."""

    granted, connector_decisions, classes = compute_allowed_tool_names(
        task=task,
        request=request,
        tool_registry=tool_registry,
        connector_tool_names=connector_tool_names,
        grantable_connector_tools=grantable_connector_tools,
        web_search_tool=web_search_tool,
        denied_tool_names=denied_tool_names,
        pack_allowed_tool_classes=pack_allowed_tool_classes,
    )
    allowed = sorted(granted)
    if prompt_tool_names is None:
        prompt_names = list(allowed)
        reduction_reason = None
    else:
        prompt_names = [name for name in prompt_tool_names if name in granted]
        reduction_reason = prompt_reduction_reason
        if set(prompt_names) != set(prompt_tool_names):
            reduction_reason = reduction_reason or "dropped_ungranted_tools"

    if repair_eligible is None:
        repair_eligible = False
    if approval_required is None:
        approval_required = False
    if external_action_requires_approval is None:
        external_action_requires_approval = False

    max_calls = max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10)
    policy = EffectiveTaskPolicy(
        task_id=task.id,
        run_id=run_id,
        pack_id=pack_id,
        pack_version=pack_version,
        pack_policy_digest=pack_policy_digest,
        capability=task.capability,
        descriptor_version=descriptor_version,
        executor_mode=executor_mode,
        executor_adapter_id=executor_adapter_id,
        allowed_tool_names=allowed,
        allowed_tool_classes=classes,
        connector_decisions=connector_decisions,
        allowed_connector_classes=sorted(allowed_connector_classes),
        allowed_connector_names=sorted(grantable_connector_tools),
        workspace_access=workspace_access_for_tool_classes(set(classes)),  # type: ignore[arg-type]
        path_scopes={
            "allowed": list(task.allowed_path_patterns),
            "readable": list(task.effective_read_patterns()),
            "writable": list(task.effective_write_patterns()),
        },
        call_limits={"max_calls": max_calls, "max_tool_calls": task.budget.max_tool_calls},
        result_limits={},
        skill_ids=list(skill_ids),
        profile_ids=list(profile_ids) if profile_ids is not None else [agent_profile],
        reference_pack_ids=list(reference_pack_ids or []),
        stack_profile_artifact_sha256=stack_profile_artifact_sha256,
        stack_profile_digest=stack_profile_digest,
        stack_profile_schema_version=stack_profile_schema_version,
        route_class=route_class,
        primary_model_profile=model_profile,
        fallback_model_profile=fallback_model_profile,
        fallback_eligible=fallback_eligible,
        budget_ceiling=task.budget.model_dump(mode="json"),
        validator_ids=list(validator_ids or []),
        repair_eligible=repair_eligible,
        approval_required=approval_required,
        external_action_requires_approval=external_action_requires_approval,
        prompt_tool_names=prompt_names,
        prompt_reduction_reason=reduction_reason,
    )
    policy.ensure_prompt_subset()
    return policy.model_copy(update={"policy_digest": policy.computed_digest()})


def grantable_connector_names_for_task(
    *,
    task: TaskSpec,
    grantable_fn,
    allowed_connector_classes: frozenset[str] | None = None,
) -> frozenset[str]:
    """Connector tools permitted by capability catalogue and operator enablement."""

    permitted = CAPABILITY_TOOL_CLASSES.get(task.capability, frozenset())
    # Connector authority is never implied by a capability. It is an exact
    # descriptor ∩ pack ∩ task request intersection.
    grant_classes = set(task.required_tool_classes) & set(permitted)
    if allowed_connector_classes is not None:
        grant_classes &= set(allowed_connector_classes)
    return frozenset(grantable_fn(grant_classes))
