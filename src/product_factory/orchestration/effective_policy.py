"""Effective task policy — one durable grant for prompt, broker, and resume."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.domain.capabilities import (
    CAPABILITY_TOOL_CLASSES,
    EXTERNAL_READ_TOOL_CLASSES,
)
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskSpec
from product_factory.tools.registry import ToolRegistry

EFFECTIVE_TASK_POLICY_SCHEMA = "effective_task_policy.v1"
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
    capability: str
    executor_mode: ExecutorMode = "model_draft"
    allowed_tool_names: list[str] = Field(default_factory=list)
    allowed_tool_classes: list[str] = Field(default_factory=list)
    connector_decisions: dict[str, str] = Field(default_factory=dict)
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

    def ensure_prompt_subset(self) -> None:
        allowed = set(self.allowed_tool_names)
        outside = [name for name in self.prompt_tool_names if name not in allowed]
        if outside:
            raise ValueError(
                f"prompt_tool_names must be a subset of allowed_tool_names; got extras {outside}"
            )


_REPOSITORY_WRITE_TOOL_NAMES = frozenset({"create_file", "apply_patch"})
_SOURCE_READ_TOOL_NAMES = frozenset({"fetch_source"})
_DECISION_ANALYSIS_TOOL_NAMES = frozenset({"compare_options"})
_READ_ONLY_STRIP_WORKFLOW_TYPES = frozenset(
    {"repository_investigation", "feasibility_discovery", "change_intake"}
)
_INTAKE_WORKFLOW_TYPES = frozenset({"change_intake"})
_QUALITY_GATE_WORKFLOW_TYPES = frozenset({"quality_gate"})


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
    """Resolve the exact broker grant and connector decisions for a task."""

    granted = {
        t.name
        for t in tool_registry.list()
        if t.tool_class in task.required_tool_classes
        or (not task.required_tool_classes and t.risk_class in {"R0", "R1"})
    }
    if task.capability in {
        "composition",
        "architecture",
        "documentation",
        "domain_research",
        "decision_analysis",
        "interface_analysis",
    }:
        granted.add("write_artifact")
    if task.capability == "decision_analysis":
        registered = {t.name for t in tool_registry.list()}
        granted |= registered & _DECISION_ANALYSIS_TOOL_NAMES
    if task.capability in {"implementation", "repair"}:
        granted = {
            "create_file",
            "apply_patch",
            "git_diff",
            "git_status",
            "read_file",
            "list_files",
            "search_text",
            "run_validation_command",
        }
    if task.capability in {"repository_analysis", "independent_review"}:
        granted.update({"read_file", "list_files", "search_text", "git_diff", "git_status"})
    if request.workflow_type in _READ_ONLY_STRIP_WORKFLOW_TYPES:
        granted -= _REPOSITORY_WRITE_TOOL_NAMES
        granted.discard("run_validation_command")
    if request.workflow_type in _INTAKE_WORKFLOW_TYPES:
        granted.discard(web_search_tool)
        granted -= _SOURCE_READ_TOOL_NAMES
    elif request.workflow_type in _QUALITY_GATE_WORKFLOW_TYPES:
        granted -= _REPOSITORY_WRITE_TOOL_NAMES

    connector_decisions: dict[str, str] = {}
    if connector_tool_names:
        granted -= connector_tool_names
        for name in sorted(connector_tool_names):
            connector_decisions[name] = "deny:not_in_capability_or_disabled"
        granted |= set(grantable_connector_tools)
        for name in sorted(grantable_connector_tools):
            connector_decisions[name] = "allow"
    granted -= denied_tool_names
    if pack_allowed_tool_classes is not None:
        tool_classes = {tool.name: tool.tool_class for tool in tool_registry.list()}
        granted = {name for name in granted if tool_classes.get(name) in pack_allowed_tool_classes}

    classes = sorted(
        {t.tool_class for t in tool_registry.list() if t.name in granted}
        | set(task.required_tool_classes)
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

    max_calls = max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10)
    policy = EffectiveTaskPolicy(
        task_id=task.id,
        run_id=run_id,
        pack_id=pack_id,
        pack_version=pack_version,
        capability=task.capability,
        executor_mode=executor_mode,
        allowed_tool_names=allowed,
        allowed_tool_classes=classes,
        connector_decisions=connector_decisions,
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
        repair_eligible=task.capability in {"implementation", "repair"},
        approval_required=True,
        prompt_tool_names=prompt_names,
        prompt_reduction_reason=reduction_reason,
    )
    policy.ensure_prompt_subset()
    return policy


def grantable_connector_names_for_task(
    *,
    task: TaskSpec,
    grantable_fn,
) -> frozenset[str]:
    """Connector tools permitted by capability catalogue and operator enablement."""

    grant_classes = set(task.required_tool_classes)
    permitted = CAPABILITY_TOOL_CLASSES.get(task.capability, frozenset())
    grant_classes |= permitted & EXTERNAL_READ_TOOL_CLASSES
    return frozenset(grantable_fn(grant_classes))
