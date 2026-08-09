"""Legacy public compatibility adapter over application lifecycle ports."""

from __future__ import annotations

from typing import Any

from product_factory.application.command_service import LifecycleCommandService
from product_factory.application.ports import RunLifecyclePort
from product_factory.application.services import ApplicationQueryService
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.executors.research_agent import (  # noqa: F401
    EVIDENCE_BUILD_TOOL_NAMES as _EVIDENCE_BUILD_TOOL_NAMES,
    RESEARCH_AGENT_MAX_ROUNDS as _RESEARCH_AGENT_MAX_ROUNDS,
    RETRIEVAL_LOOP_TOOL_NAMES as _RETRIEVAL_LOOP_TOOL_NAMES,
    SOURCE_READ_TOOL_NAMES as _SOURCE_READ_TOOL_NAMES,
)
from product_factory.orchestration.composition.service import (  # noqa: F401
    append_markdown_continuation,
    output_was_truncated,
)
from product_factory.orchestration.implementation_helpers import (  # noqa: F401
    deterministic_impl_files,
    extract_unified_diff,
)
from product_factory.registry.capability_descriptors import CAPABILITY_DESCRIPTORS
from product_factory.scheduling.scheduler import WaveScheduler

_DISCOVERY_CAPABILITIES = frozenset(
    capability_id
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
    if descriptor.executor_mode == "research_agent_loop"
    and "evidence_build" in descriptor.permissible_tool_classes
)
_RESEARCH_LOOP_CAPABILITIES = frozenset(
    capability_id
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
    if descriptor.executor_mode == "research_agent_loop"
)


def default_code_change_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_code_change_plan as plan

    return plan(request_text)


def default_architecture_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_architecture_plan as plan

    return plan(request_text)


def default_technical_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_technical_plan as plan

    return plan(request_text)


def default_investigation_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_investigation_plan as plan

    return plan(request_text)


def default_quality_gate_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_quality_gate_plan as plan

    return plan(request_text)


def default_release_readiness_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_release_readiness_plan as plan

    return plan(request_text)


def default_feasibility_discovery_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_feasibility_discovery_plan as plan

    return plan(request_text)


def default_change_intake_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_change_intake_plan as plan

    return plan(request_text)


def transitive_dependencies(plan, task_id: str):
    return WaveScheduler().transitive_dependencies(plan, task_id)


class RunCoordinator:
    """Compatibility facade; construction and implementation live elsewhere."""

    def __init__(
        self,
        *,
        lifecycle: RunLifecyclePort,
        commands: LifecycleCommandService,
        queries: ApplicationQueryService,
    ) -> None:
        self._lifecycle = lifecycle
        self.commands = commands
        self.queries = queries

    def run(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest:
        return self._lifecycle.run(request, run_id=run_id)

    def resume(self, run_id: str) -> RunManifest:
        return self._lifecycle.resume(run_id)

    def approve(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        return self._lifecycle.approve(run_id, apply=apply)

    def reject(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle.reject(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle.cancel(run_id)

    def revise(self, run_id: str, *, note: str) -> RunManifest:
        return self._lifecycle.revise(run_id, note=note)

    def apply_patch(self, run_id: str) -> dict[str, Any]:
        return self._lifecycle.apply_patch(run_id)
