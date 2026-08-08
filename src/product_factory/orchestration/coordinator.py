"""RunCoordinator — thin lifecycle compatibility façade (SD2 / G2).

All behavior lives in RunLifecycleEngine and owning services. Do not add
workflow implementations, model/tool loops, or composition helpers here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.executors.research_agent import (  # noqa: F401
    EVIDENCE_BUILD_TOOL_NAMES as _EVIDENCE_BUILD_TOOL_NAMES,
    RESEARCH_AGENT_MAX_ROUNDS as _RESEARCH_AGENT_MAX_ROUNDS,
    RETRIEVAL_LOOP_TOOL_NAMES as _RETRIEVAL_LOOP_TOOL_NAMES,
    SOURCE_READ_TOOL_NAMES as _SOURCE_READ_TOOL_NAMES,
)
from product_factory.gateway.base import ModelGateway
from product_factory.orchestration.composition.service import (  # noqa: F401
    append_markdown_continuation,
    output_was_truncated,
)
from product_factory.orchestration.implementation_helpers import (  # noqa: F401
    deterministic_impl_files,
    extract_unified_diff,
)
from product_factory.orchestration.lifecycle import engine as _lifecycle_engine
from product_factory.orchestration.lifecycle.engine import RunLifecycleEngine
from product_factory.scheduling.scheduler import WaveScheduler

# Descriptor-derived capability sets (compat for discovery tests).
_DISCOVERY_CAPABILITIES = _lifecycle_engine._DISCOVERY_CAPABILITIES
_RESEARCH_LOOP_CAPABILITIES = _lifecycle_engine._RESEARCH_LOOP_CAPABILITIES


def default_code_change_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_code_change_plan as _plan

    return _plan(request_text)


def default_architecture_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_architecture_plan as _plan

    return _plan(request_text)


def default_technical_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_technical_plan as _plan

    return _plan(request_text)


def default_investigation_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_investigation_plan as _plan

    return _plan(request_text)


def default_quality_gate_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_quality_gate_plan as _plan

    return _plan(request_text)


def default_release_readiness_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_release_readiness_plan as _plan

    return _plan(request_text)


def default_feasibility_discovery_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import (
        default_feasibility_discovery_plan as _plan,
    )

    return _plan(request_text)


def default_change_intake_plan(request_text: str) -> PlannerOutput:
    from product_factory.workflows.default_plans import default_change_intake_plan as _plan

    return _plan(request_text)


def transitive_dependencies(plan, task_id: str):
    return WaveScheduler().transitive_dependencies(plan, task_id)


class RunCoordinator:
    """Compatibility façade over RunLifecycleEngine."""

    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        data_dir: Path | None = None,
        use_deterministic_planner: bool = False,
    ) -> None:
        self._engine = RunLifecycleEngine(
            config=config,
            gateway=gateway,
            data_dir=data_dir,
            use_deterministic_planner=use_deterministic_planner,
        )
        # HostService and tests read these attributes directly.
        self.config = self._engine.config
        self.pf_root = self._engine.pf_root
        self.db = self._engine.db
        self.skills = self._engine.skills
        self.tool_registry = self._engine.tool_registry
        self.connector_registry = self._engine.connector_registry
        self.connector_broker = self._engine.connector_broker
        self.allow_deterministic_workers = self._engine.allow_deterministic_workers
        self.use_deterministic_planner = self._engine.use_deterministic_planner
        self._raw_gateway = self._engine._raw_gateway

    def run(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest:
        return self._engine.run(request, run_id=run_id)

    def resume(self, run_id: str) -> RunManifest:
        return self._engine.resume(run_id)

    def approve(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        return self._engine.approve(run_id, apply=apply)

    def reject(self, run_id: str) -> dict[str, Any]:
        return self._engine.reject(run_id)

    def cancel(self, run_id: str) -> dict[str, Any]:
        return self._engine.cancel(run_id)

    def revise(self, run_id: str, *, note: str) -> RunManifest:
        return self._engine.revise(run_id, note=note)

    def apply_patch(self, run_id: str) -> dict[str, Any]:
        return self._engine.apply_patch(run_id)

    # Compatibility delegates so existing tests can monkeypatch class methods.
    def _build_execution_context(self, *args, **kwargs):
        return self._engine._build_execution_context(*args, **kwargs)

    def _plan(self, *args, **kwargs):
        return self._engine._plan(*args, **kwargs)

    def _execute(self, *args, **kwargs):
        return self._engine._execute(*args, **kwargs)

    def _execute_task(self, *args, **kwargs):
        return self._engine._execute_task(*args, **kwargs)

    def _raise_if_cancelled(self, *args, **kwargs):
        return self._engine._raise_if_cancelled(*args, **kwargs)

    def _apply_expected_file_guidance(self, *args, **kwargs):
        return self._engine._apply_expected_file_guidance(*args, **kwargs)

    def __getattr__(self, name: str):
        """Delegate remaining attributes to the lifecycle engine (compat)."""
        return getattr(self._engine, name)
