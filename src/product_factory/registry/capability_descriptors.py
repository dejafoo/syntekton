"""Authoritative capability descriptor registry (SD1.A).

Packs may narrow tool authority declared here; they may never widen it.
Unknown descriptor fields fail at registration/compilation, before a run
is admitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from product_factory.domain.errors import ConfigurationError

EvaluationCategory = Literal[
    "planning",
    "implementation",
    "review",
    "quality",
    "research",
    "operations",
    "composition",
    "documentation",
]

ExecutorModeName = Literal[
    "deterministic",
    "model_draft",
    "repository_agent_loop",
    "research_agent_loop",
    "interface_agent_loop",
    "validation",
    "composition",
]

KNOWN_EXECUTOR_ADAPTERS: frozenset[str] = frozenset(
    {
        "repository_inventory",
        "deployment_state_machine",
        "repository_agent",
        "research_agent",
        "interface_agent",
        "independent_review",
        "security_review",
        "documentation",
        "test_design",
        "release_analysis",
        "operations_analysis",
        "test_execution",
        "composition",
    }
)

KNOWN_AGENT_PROFILES: frozenset[str] = frozenset(
    {
        "planner",
        "repository_explorer",
        "implementation_worker",
        "security_reviewer",
        "test_worker",
        "independent_reviewer",
        "composer",
        "researcher",
        "decision_analyst",
        "interface_analyst",
        "release_analyst",
        "operations_analyst",
        "deployment_controller",
    }
)

KNOWN_MODEL_ROLES: frozenset[str] = frozenset(
    {
        "supervisor",
        "coding_worker",
        "fast_worker",
        "local_target_reviewer",
    }
)

KNOWN_EVALUATION_CATEGORIES: frozenset[str] = frozenset(
    {
        "planning",
        "implementation",
        "review",
        "quality",
        "research",
        "operations",
        "composition",
        "documentation",
    }
)

# Tool classes that may appear on any descriptor. Kept here so registration
# fails closed for typos before packs compile.
KNOWN_TOOL_CLASSES: frozenset[str] = frozenset(
    {
        "repository_read",
        "repository_write",
        "git_read",
        "git_write",
        "validation_command",
        "artifact_write",
        "web_read",
        "mcp_filesystem_read",
        "ci_read",
        "ops_read",
        "source_read",
        "evidence_build",
        "interface_analysis",
        "synthetic_write",
        "deployment_read",
        "deployment_write",
    }
)

EXTERNAL_READ_TOOL_CLASSES: frozenset[str] = frozenset(
    {"web_read", "mcp_filesystem_read", "ci_read", "ops_read"}
)
DEPLOYMENT_TOOL_CLASSES: frozenset[str] = frozenset({"deployment_read", "deployment_write"})


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    """Trusted per-capability authority. Packs narrow; they do not invent."""

    id: str
    version: str
    executor_mode: ExecutorModeName
    executor_adapter_id: str
    agent_profile_id: str
    default_model_role: str
    permissible_tool_classes: frozenset[str]
    result_schema_id: str
    default_budget: dict[str, object]
    evaluation_category: EvaluationCategory
    parser_id: str = "passthrough.v1"


def _budget(
    *,
    max_tool_calls: int = 40,
    max_input_tokens: int = 28_000,
    max_output_tokens: int = 8_000,
    max_cost_usd: str = "1.00",
) -> dict[str, object]:
    return {
        "max_tool_calls": max_tool_calls,
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "max_cost_usd": Decimal(max_cost_usd),
        "max_repair_attempts": 2,
        "max_wall_clock_seconds": 600,
    }


def _desc(
    capability_id: str,
    *,
    executor_mode: ExecutorModeName,
    executor_adapter_id: str,
    agent_profile_id: str,
    default_model_role: str,
    tool_classes: frozenset[str],
    result_schema_id: str,
    evaluation_category: EvaluationCategory,
    parser_id: str = "passthrough.v1",
    version: str = "1",
    budget: dict[str, object] | None = None,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        id=capability_id,
        version=version,
        executor_mode=executor_mode,
        executor_adapter_id=executor_adapter_id,
        agent_profile_id=agent_profile_id,
        default_model_role=default_model_role,
        permissible_tool_classes=tool_classes,
        result_schema_id=result_schema_id,
        default_budget=budget or _budget(),
        evaluation_category=evaluation_category,
        parser_id=parser_id,
    )


CAPABILITY_DESCRIPTORS: dict[str, CapabilityDescriptor] = {
    "requirements": _desc(
        "requirements",
        executor_mode="research_agent_loop",
        executor_adapter_id="research_agent",
        agent_profile_id="repository_explorer",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read"}),
        result_schema_id="technical_plan.document.v1",
        evaluation_category="planning",
    ),
    "architecture": _desc(
        "architecture",
        executor_mode="research_agent_loop",
        executor_adapter_id="research_agent",
        agent_profile_id="composer",
        default_model_role="supervisor",
        tool_classes=frozenset({"repository_read", "artifact_write"}) | EXTERNAL_READ_TOOL_CLASSES,
        result_schema_id="technical_plan.document.v1",
        evaluation_category="planning",
        budget=_budget(max_output_tokens=8_000, max_tool_calls=48),
    ),
    "repository_analysis": _desc(
        "repository_analysis",
        executor_mode="deterministic",
        executor_adapter_id="repository_inventory",
        agent_profile_id="repository_explorer",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read", "git_read"}) | EXTERNAL_READ_TOOL_CLASSES,
        result_schema_id="repository_analysis.report.v1",
        evaluation_category="research",
        parser_id="repository_inventory.v1",
    ),
    "implementation": _desc(
        "implementation",
        executor_mode="repository_agent_loop",
        executor_adapter_id="repository_agent",
        agent_profile_id="implementation_worker",
        default_model_role="coding_worker",
        tool_classes=frozenset(
            {
                "repository_read",
                "repository_write",
                "git_read",
                "git_write",
                "validation_command",
            }
        ),
        result_schema_id="change_set.patch.v1",
        evaluation_category="implementation",
        parser_id="unified_diff.v1",
    ),
    "security_review": _desc(
        "security_review",
        executor_mode="model_draft",
        executor_adapter_id="security_review",
        agent_profile_id="security_reviewer",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read", "git_read"}) | EXTERNAL_READ_TOOL_CLASSES,
        result_schema_id="security_evidence.document.v1",
        evaluation_category="review",
        parser_id="security_findings.v1",
    ),
    "test_design": _desc(
        "test_design",
        executor_mode="model_draft",
        executor_adapter_id="test_design",
        agent_profile_id="test_worker",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read", "repository_write", "validation_command"})
        | EXTERNAL_READ_TOOL_CLASSES,
        result_schema_id="test_plan.document.v1",
        evaluation_category="quality",
        parser_id="test_plan.v1",
    ),
    "test_execution": _desc(
        "test_execution",
        executor_mode="validation",
        executor_adapter_id="test_execution",
        agent_profile_id="test_worker",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read", "validation_command"}),
        result_schema_id="quality_findings.document.v1",
        evaluation_category="quality",
        parser_id="validation_receipt.v1",
    ),
    "documentation": _desc(
        "documentation",
        executor_mode="model_draft",
        executor_adapter_id="documentation",
        agent_profile_id="composer",
        default_model_role="fast_worker",
        tool_classes=frozenset({"repository_read", "artifact_write"}),
        result_schema_id="technical_plan.document.v1",
        evaluation_category="documentation",
        parser_id="document_draft.v1",
    ),
    "composition": _desc(
        "composition",
        executor_mode="composition",
        executor_adapter_id="composition",
        agent_profile_id="composer",
        default_model_role="supervisor",
        tool_classes=frozenset({"repository_read", "artifact_write", "git_read"}),
        result_schema_id="change_set.patch.v1",
        evaluation_category="composition",
        parser_id="composition.v1",
    ),
    "independent_review": _desc(
        "independent_review",
        executor_mode="model_draft",
        executor_adapter_id="independent_review",
        agent_profile_id="independent_reviewer",
        default_model_role="local_target_reviewer",
        tool_classes=frozenset({"repository_read", "git_read"}),
        result_schema_id="quality_findings.document.v1",
        evaluation_category="review",
        parser_id="review_findings.v1",
    ),
    "repair": _desc(
        "repair",
        executor_mode="repository_agent_loop",
        executor_adapter_id="repository_agent",
        agent_profile_id="implementation_worker",
        default_model_role="coding_worker",
        tool_classes=frozenset(
            {
                "repository_read",
                "repository_write",
                "git_read",
                "git_write",
                "validation_command",
            }
        ),
        result_schema_id="change_set.patch.v1",
        evaluation_category="implementation",
        parser_id="unified_diff.v1",
    ),
    "domain_research": _desc(
        "domain_research",
        executor_mode="research_agent_loop",
        executor_adapter_id="research_agent",
        agent_profile_id="researcher",
        default_model_role="fast_worker",
        tool_classes=frozenset(
            {"repository_read", "artifact_write", "web_read", "source_read", "evidence_build"}
        ),
        result_schema_id="evidence_report.document.v2",
        evaluation_category="research",
    ),
    "decision_analysis": _desc(
        "decision_analysis",
        executor_mode="research_agent_loop",
        executor_adapter_id="research_agent",
        agent_profile_id="decision_analyst",
        default_model_role="fast_worker",
        tool_classes=frozenset({"artifact_write", "evidence_build"}),
        result_schema_id="decision_record.v1",
        evaluation_category="research",
    ),
    "interface_analysis": _desc(
        "interface_analysis",
        executor_mode="interface_agent_loop",
        executor_adapter_id="interface_agent",
        agent_profile_id="interface_analyst",
        default_model_role="fast_worker",
        tool_classes=frozenset(
            {"repository_read", "artifact_write", "interface_analysis", "synthetic_write"}
        ),
        result_schema_id="spike_result.v1",
        evaluation_category="research",
        parser_id="interface_evidence.v1",
    ),
    "release_analysis": _desc(
        "release_analysis",
        executor_mode="model_draft",
        executor_adapter_id="release_analysis",
        agent_profile_id="release_analyst",
        default_model_role="fast_worker",
        tool_classes=frozenset(
            {"repository_read", "git_read", "artifact_write", "ci_read", "ops_read"}
        ),
        result_schema_id="release_plan.v1",
        evaluation_category="operations",
        parser_id="release_analysis.v1",
    ),
    "operations_analysis": _desc(
        "operations_analysis",
        executor_mode="model_draft",
        executor_adapter_id="operations_analysis",
        agent_profile_id="operations_analyst",
        default_model_role="fast_worker",
        tool_classes=frozenset({"artifact_write", "ops_read"}),
        result_schema_id="operational_record.v1",
        evaluation_category="operations",
        parser_id="operations_analysis.v1",
    ),
    "deployment_execution": _desc(
        "deployment_execution",
        executor_mode="deterministic",
        executor_adapter_id="deployment_state_machine",
        agent_profile_id="deployment_controller",
        default_model_role="supervisor",
        tool_classes=frozenset({"artifact_write"}) | DEPLOYMENT_TOOL_CLASSES,
        result_schema_id="deployment_record.v1",
        evaluation_category="operations",
        parser_id="deployment_receipt.v1",
    ),
}


def validate_descriptor_catalog(
    descriptors: dict[str, CapabilityDescriptor] | None = None,
) -> None:
    """Fail closed if the catalog references unknown adapters/profiles/modes."""

    catalog = descriptors if descriptors is not None else CAPABILITY_DESCRIPTORS
    problems: list[str] = []
    for capability_id, descriptor in catalog.items():
        if descriptor.id != capability_id:
            problems.append(f"{capability_id}: id mismatch {descriptor.id!r}")
        if descriptor.executor_adapter_id not in KNOWN_EXECUTOR_ADAPTERS:
            problems.append(f"{capability_id}: unknown adapter {descriptor.executor_adapter_id!r}")
        if descriptor.agent_profile_id not in KNOWN_AGENT_PROFILES:
            problems.append(f"{capability_id}: unknown profile {descriptor.agent_profile_id!r}")
        if descriptor.default_model_role not in KNOWN_MODEL_ROLES:
            problems.append(f"{capability_id}: unknown model role {descriptor.default_model_role!r}")
        if descriptor.evaluation_category not in KNOWN_EVALUATION_CATEGORIES:
            problems.append(
                f"{capability_id}: unknown evaluation category {descriptor.evaluation_category!r}"
            )
        unknown_tools = set(descriptor.permissible_tool_classes) - KNOWN_TOOL_CLASSES
        if unknown_tools:
            problems.append(f"{capability_id}: unknown tool classes {sorted(unknown_tools)}")
        if not descriptor.result_schema_id:
            problems.append(f"{capability_id}: missing result_schema_id")
    if problems:
        raise ConfigurationError(f"Invalid capability descriptor catalog: {problems}")


def require_descriptor(capability: str) -> CapabilityDescriptor:
    try:
        return CAPABILITY_DESCRIPTORS[capability]
    except KeyError as exc:
        raise ConfigurationError(f"Unknown capability descriptor: {capability!r}") from exc


def descriptor_for(capability: str) -> CapabilityDescriptor | None:
    return CAPABILITY_DESCRIPTORS.get(capability)


def executor_mode_for(capability: str) -> ExecutorModeName:
    return require_descriptor(capability).executor_mode


def executor_adapter_for(capability: str) -> str:
    return require_descriptor(capability).executor_adapter_id


def agent_profile_for(capability: str) -> str:
    return require_descriptor(capability).agent_profile_id


def model_role_for(capability: str) -> str:
    return require_descriptor(capability).default_model_role


def permissible_tool_classes_for(capability: str) -> frozenset[str]:
    return require_descriptor(capability).permissible_tool_classes


def result_schema_for(capability: str) -> str:
    return require_descriptor(capability).result_schema_id


def evaluation_category_for(capability: str) -> EvaluationCategory:
    return require_descriptor(capability).evaluation_category


def default_budget_for(capability: str) -> dict[str, object]:
    return dict(require_descriptor(capability).default_budget)


# Validate at import so a broken catalog never silently ships.
validate_descriptor_catalog()
