"""RunLifecycleEngine — submit/execute/resume/cancel/revise/approve orchestration (SD2)."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.connectors.broker import EVENT_INVOKED as CONNECTOR_EVENT_INVOKED
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.defaults import default_connector_registry
from product_factory.connectors.source_ledger import SourceLedger
from product_factory.connectors.tavily import CONNECTOR_ID as TAVILY_CONNECTOR_ID
from product_factory.connectors.tavily import TOOL_WEB_SEARCH
from product_factory.context.assembler import (
    assemble_context,
    list_repository_paths,
    resolve_context_limits,
    select_repository_excerpts,
)
from product_factory.context.task_context import build_task_context, persist_task_context
from product_factory.domain.artifacts import ResourceRef
from product_factory.domain.budgets import TaskBudgetDefaults, clamp_task_budget
from product_factory.domain.errors import (
    ApprovalBlockedError,
    BudgetExhaustedError,
    ConfigurationError,
    PlanRejectedError,
    RunCancelledError,
    RuntimeFailureError,
    SkillGrantViolation,
    ToolAuthorizationError,
)
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.plans import CompiledPlan, PlannerOutput
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskResult, TaskSpec
from product_factory.domain.tools import CapabilityGrant
from product_factory.domain.usage import UsageMetrics
from product_factory.executors import TaskExecutionRequest, execute_task
from product_factory.executors.research_agent import (
    EVIDENCE_BUILD_TOOL_NAMES as _EVIDENCE_BUILD_TOOL_NAMES,
)
from product_factory.executors.research_agent import (
    SOURCE_READ_TOOL_NAMES as _SOURCE_READ_TOOL_NAMES,
)
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.observability.contracts import EventSeverity
from product_factory.observability.events import EventLog
from product_factory.observability.otel import maybe_create_otel_bridge
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.budget_ledger import BudgetLedger, warn_unused_profile_set
from product_factory.orchestration.composition.service import CompositionService
from product_factory.orchestration.concurrency import run_wave
from product_factory.orchestration.effective_policy import (
    EFFECTIVE_TASK_POLICY_SCHEMA,
    EffectiveTaskPolicy,
    compute_allowed_tool_names,
    grantable_connector_names_for_task,
    resolve_effective_task_policy,
)
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.orchestration.finalization.run_finalizer import RunFinalizer
from product_factory.orchestration.repair import (
    create_repair_tasks,
    patch_fingerprint,
    should_terminate_no_progress,
    update_no_progress,
)
from product_factory.orchestration.skill_grants import enforce_skill_grants
from product_factory.orchestration.validation_repair.service import (
    ValidationRepairService,
    changed_files_from_patch,
    resolve_validation_command_ids,
)
from product_factory.orchestration.worktree_lineage import WorktreeLineageService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.planning.compiler import compile_plan
from product_factory.planning.planner import plan_with_gateway
from product_factory.policy.composition_gates import evaluate_composition_gates
from product_factory.policy.domain_packs import resolve_request_domain_packs
from product_factory.policy.policy_profiles import resolve_request_policy_profiles
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    agent_profile_for,
    require_descriptor,
)
from product_factory.repositories.patches import (
    apply_patch,
    create_patch,
)
from product_factory.repositories.snapshot import snapshot_repository
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.repository.stack_profile import StackProfile, discover_stack_profile
from product_factory.scheduling.scheduler import (
    WaveScheduler,
    resolve_task_model_profile,
)
from product_factory.schemas import validate_write_payload
from product_factory.skills.profiles import ProfileRegistry
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry
from product_factory.validation.pipeline import (
    has_blocking_failures,
    request_expects_web_citations,
    validate_architecture_document,
    validate_architecture_request_specificity,
    validate_citations,
    validate_deployment_record,
    validate_document_sections,
    validate_intake_no_invention,
    validate_intake_sections,
    validate_json_contract,
    validate_operational_record,
    validate_option_comparison,
    validate_recommendation,
    validate_regulated_claims,
    validate_release_plan,
    validate_research_provenance,
    validate_secrets,
    validate_verification_report,
    validate_web_search_used,
)
from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ROLE_CHANGE_BRIEF,
    ROLE_CHANGE_SET,
    ROLE_CLARIFICATION_REQUEST,
    ROLE_EVIDENCE_REPORT,
    ROLE_FEASIBILITY_DOSSIER,
    ROLE_PROPOSED_PATCH,
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
    ROLE_VERIFICATION_REPORT,
    ArtifactLandMap,
)
from product_factory.workflows.base import WorkflowPack
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.handlers.base import ComposeContext
from product_factory.workflows.handoffs import validate_pack_handoffs
from product_factory.workflows.inputs import persist_pack_input, validate_pack_input
from product_factory.workflows.registry import (
    is_registered_workflow,
    land_map_for_request,
    resolve_workflow_pack,
)

logger = logging.getLogger("product_factory.orchestration.lifecycle")

# Named workflow frozensets removed (SD2): pack output roles / validators decide.


# Descriptor-derived aliases (exclude interface_agent_loop).
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


# Quality-gate deliverable roles and their fallback names. Keyed by role so a
# composer task resolves its document from the plan, not from the workflow id.
_QUALITY_GATE_ROLES: dict[str, str] = {
    ROLE_TEST_PLAN: "TEST_PLAN.md",
    ROLE_QUALITY_FINDINGS: "QUALITY_FINDINGS.md",
    ROLE_SECURITY_EVIDENCE: "SECURITY_EVIDENCE.md",
    ROLE_FEASIBILITY_DOSSIER: "FEASIBILITY_DISCOVERY.md",
    ROLE_CHANGE_BRIEF: "CHANGE_BRIEF.md",
    ROLE_CLARIFICATION_REQUEST: "CLARIFICATION_REQUEST.md",
}


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


def _clamp_proposal_budgets(proposal: PlannerOutput, config: AppConfig) -> PlannerOutput:
    """Apply policy floors/ceilings to every task budget in a planned DAG."""
    defaults = getattr(config.policies, "budgets", None)
    task_defaults: TaskBudgetDefaults = (
        defaults.task if defaults is not None else TaskBudgetDefaults()
    )
    clamped = [
        task.model_copy(update={"budget": clamp_task_budget(task.budget, defaults=task_defaults)})
        for task in proposal.tasks
    ]
    return proposal.model_copy(update={"tasks": clamped})


from product_factory.orchestration.implementation_helpers import (  # noqa: E402
    deterministic_impl_files,
)


class RunLifecycleEngine:
    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        data_dir: Path | None = None,
        use_deterministic_planner: bool = False,
    ) -> None:
        self.config = config
        self.allow_deterministic_workers = isinstance(gateway, MockGateway)
        self.use_deterministic_planner = use_deterministic_planner or isinstance(
            gateway, MockGateway
        )
        self.pf_root = data_dir or (config.root / ".product-factory")
        self.pf_root.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.pf_root / "data" / "product_factory.sqlite")
        self.skills = SkillRegistry.load(config.root / "skills")
        self.tool_registry = default_tool_registry()
        self.connector_registry = default_connector_registry(
            config.connectors,
            config_root=config.root,
            deployment_state_path=self.pf_root / "deployments" / "staging-state.json",
        )
        # Connector tools share the one registry so `ToolBroker.execute` resolves
        # and trust-labels them exactly like built-in tools.
        for definition in self.connector_registry.tool_definitions():
            self.tool_registry.register(definition)
        self.connector_broker = ConnectorBroker(
            self.connector_registry,
            config=config.connectors,
            mock=isinstance(gateway, MockGateway),
        )
        # The provider adapter is immutable shared configuration. Instrumented
        # gateways are constructed per run and live only in RunExecutionContext.
        self._raw_gateway = (
            gateway.inner if isinstance(gateway, InstrumentedModelGateway) else gateway
        )
        self.composition = CompositionService(config=config, gateway=self._raw_gateway)
        self.validation_repair = ValidationRepairService(
            config=config, raw_gateway=self._raw_gateway
        )
        self.wave_scheduler = WaveScheduler()
        self.worktree_lineage = WorktreeLineageService()
        self.finalizer = RunFinalizer()

    def _deployment_approval_verified(
        self,
        request: RunRequest,
        *,
        consumer_run_id: str,
        capability: str,
    ) -> bool:
        """Resolve durable ActionApproval for deployment_execution (SD0.C).

        Pack-input booleans and mirrored digest fields are never authority.
        Temporary on RunCoordinator until the deployment executor owns this
        (issue: remove-coordinator-approval-verify-2026-08).
        """
        if capability != "deployment_execution" or request.workflow_type != "deployment_execution":
            return False
        binding = request.pack_input.get("approval_binding")
        if not isinstance(binding, dict):
            binding = {}
        approval_id = str(
            binding.get("approval_id") or request.pack_input.get("approval_id") or ""
        ).strip()
        if not approval_id:
            return False
        from product_factory.trust.approvals import (
            ApprovalError,
            ApprovalService,
            deployment_action_fingerprint,
        )

        release_handoff_id = str(
            binding.get("release_handoff_id") or request.pack_input.get("release_handoff_id") or ""
        )
        release_handoff_digest = str(
            binding.get("release_handoff_digest")
            or request.pack_input.get("release_handoff_digest")
            or ""
        )
        try:
            expected = deployment_action_fingerprint(
                release_handoff_id=release_handoff_id,
                release_handoff_digest=release_handoff_digest,
                release_plan_digest=str(request.pack_input.get("release_plan_digest") or ""),
                artifact_digest=str(request.pack_input.get("artifact_digest") or ""),
                target_id=str(request.pack_input.get("target_id") or ""),
                change_window=request.pack_input.get("change_window"),
                idempotency_key=str(request.pack_input.get("idempotency_key") or ""),
            )
            ApprovalService(self.db).consume_for_execution(
                approval_id,
                expected_fingerprint=expected,
                consumer_run_id=consumer_run_id,
            )
        except ApprovalError:
            return False
        return True

    def _build_execution_context(
        self,
        *,
        run_id: str,
        request: RunRequest,
        run_dir: Path,
        budget_snapshot: dict[str, Any] | None = None,
        workflow_pack: WorkflowPack | None = None,
    ) -> RunExecutionContext:
        events = EventLog(run_dir / "events.jsonl")
        artifacts = ArtifactStore(run_dir / "artifacts")
        recorder = TelemetryRecorder(
            self.db,
            jsonl=events,
            content_dir=run_dir / "content",
            otel_exporter=maybe_create_otel_bridge(),
        )
        ledger = (
            BudgetLedger.restore(request.budget, budget_snapshot)
            if budget_snapshot
            else BudgetLedger(request.budget)
        )
        gateway = InstrumentedModelGateway(
            self._raw_gateway,
            recorder=recorder,
            db=self.db,
            ledger=ledger,
        )
        workspace_key = (
            str(request.repository_path.resolve())
            if request.repository_path is not None
            else (
                f"{request.workspace.repository_id}:{request.workspace.ref}"
                if request.workspace is not None
                else request.repository_id
            )
        )
        return RunExecutionContext(
            run_id=run_id,
            workflow_type=request.workflow_type,
            run_dir=run_dir,
            gateway=gateway,
            recorder=recorder,
            ledger=ledger,
            artifacts=artifacts,
            events=events,
            cancel_check=lambda: self._raise_if_cancelled(run_id),
            pack_id=workflow_pack.id if workflow_pack else None,
            pack_version=workflow_pack.version if workflow_pack else None,
            workspace_key=workspace_key,
        )

    def run(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest:
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        run_dir = self.pf_root / "runs" / run_id
        for sub in (
            "input",
            "worktrees",
            "scratch",
            "artifacts",
            "findings",
            "prompts",
            "output",
            "content",
        ):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)

        execution_context = self._build_execution_context(
            run_id=run_id,
            request=request,
            run_dir=run_dir,
        )
        events = execution_context.events
        artifacts = execution_context.artifacts
        recorder = execution_context.recorder
        note = warn_unused_profile_set(request.model_profile_set)
        if note:
            logger.warning(note)
            recorder.emit(
                run_id=run_id,
                event_type="run.deprecation_warning",
                severity=EventSeverity.WARNING,
                summary=note,
                payload={"field": "model_profile_set", "value": request.model_profile_set},
            )
        recorder.emit(
            run_id=run_id,
            event_type="run.started",
            summary="Run started",
            payload={"workflow": request.workflow_type},
        )
        self._raise_if_cancelled(run_id)

        self.db.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status="initializing",
            request=request.model_dump(mode="json"),
            active_operation="initializing",
        )

        (run_dir / "input" / "request.md").write_text(request.request_text, encoding="utf-8")
        (run_dir / "input" / "request.json").write_text(
            request.model_dump_json(indent=2), encoding="utf-8"
        )
        persist_pack_input(request.pack_input, run_dir / "input")

        usage = UsageMetrics()
        base_commit: str | None = None
        repo_summary: dict[str, Any] | None = None
        worktrees: WorktreeManager | None = None
        original_repo: Path | None = None
        # Resolve the declarative workflow pack up front (P1.G / P3.D): unknown
        # workflow ids fail closed before any planning/execution spend, and
        # the resolved pack's identity is stamped on the run manifest.
        workflow_pack: WorkflowPack | None = None
        land_map = ArtifactLandMap()
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            validate_pack_handoffs(request, workflow_pack)
            if request.handoff_refs:
                # SD0.B temporary: remove when RunLifecycleEngine owns handoff
                # resolution (issue: remove-coordinator-handoff-resolve-2026-08).
                from product_factory.trust.handoffs import HandoffService

                resolved_handoffs = HandoffService(self.db, self.pf_root).resolve_refs(
                    request,
                    workflow_pack,
                    consumer_run_id=run_id,
                    materialize_dir=run_dir / "input",
                )
                (run_dir / "input" / "resolved-handoffs.json").write_text(
                    json.dumps(
                        [item.model_dump(mode="json") for item in resolved_handoffs],
                        indent=2,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            # Bad artifact overrides or typed pack input fail here, before any
            # planning spend.
            validate_pack_input(workflow_pack, request.pack_input)
            land_map = land_map_for_request(request)
            recorder.emit(
                run_id=run_id,
                event_type="workflow.pack_resolved",
                summary=f"Workflow pack {workflow_pack.id}@{workflow_pack.version}",
                payload={
                    **workflow_pack.manifest_metadata(),
                    "artifact_land_map": land_map.as_payload(),
                },
            )
        execution_context = execution_context.with_pack(workflow_pack)
        source_policy = resolve_request_source_policy(
            request, profiles_root=self.config.root / "profiles"
        )
        SourceLedger.for_run(run_dir).record_seed_urls(
            request.pack_input.get("seed_source_urls") or (),
            policy=source_policy,
            task_id="run-input",
        )

        try:
            if request.repository_path is not None:
                snap = snapshot_repository(
                    request.repository_path,
                    allow_dirty=self.config.policies.allow_dirty_repo,
                    output_dir=run_dir / "input",
                )
                base_commit = snap.base_commit
                if (
                    request.workspace_provenance is not None
                    and base_commit != request.workspace_provenance.commit
                ):
                    raise ConfigurationError(
                        "Prepared workspace revision changed before execution",
                        details={
                            "expected_commit": request.workspace_provenance.commit,
                            "actual_commit": base_commit,
                        },
                    )
                repo_summary = snap.manifest
                original_repo = snap.repository_path
                worktrees = WorktreeManager(snap.repository_path, run_dir / "worktrees")
                recorder.emit(
                    run_id=run_id,
                    event_type="repository.snapshot",
                    summary="Repository snapshot",
                    payload={"base_commit": base_commit},
                )

            # Planning
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="planning",
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                active_operation="planning",
            )
            recorder.emit(
                run_id=run_id,
                event_type="run.status_changed",
                summary="Planning",
                payload={"status": "planning"},
            )
            proposal = self._plan(
                run_id,
                request,
                repo_summary,
                execution_context=execution_context,
            )
            art = artifacts.put_json(
                proposal.model_dump(mode="json"),
                logical_name="plan.json",
                created_by_task_id="plan",
            )
            shutil.copy(
                artifacts.blobs / art.sha256,
                run_dir / "output" / "plan.json",
            )

            compile_result = compile_plan(
                proposal,
                max_tasks=request.budget.max_tasks,
                max_parallel_tasks=request.budget.max_parallel_tasks,
                workflow_pack=workflow_pack,
                skill_registry=self.skills,
                profile_digests=self._profile_digests(request),
            )
            plan_attempt = 1
            if not compile_result.ok:
                recorder.emit(
                    run_id=run_id,
                    event_type="plan.rejected",
                    severity=EventSeverity.WARNING,
                    summary="Plan rejected by compiler",
                    payload={"errors": [e.model_dump() for e in compile_result.errors]},
                )
                if plan_attempt <= request.budget.max_plan_repairs:
                    proposal = self._plan(
                        run_id,
                        request,
                        repo_summary,
                        repair_errors=[e.model_dump() for e in compile_result.errors],
                        execution_context=execution_context,
                    )
                    compile_result = compile_plan(
                        proposal,
                        max_tasks=request.budget.max_tasks,
                        max_parallel_tasks=request.budget.max_parallel_tasks,
                        workflow_pack=workflow_pack,
                        skill_registry=self.skills,
                        profile_digests=self._profile_digests(request),
                    )
                    plan_attempt += 1
                if not compile_result.ok:
                    (run_dir / "output" / "compiler-report.json").write_text(
                        json.dumps(
                            {
                                "ok": False,
                                "errors": [e.model_dump() for e in compile_result.errors],
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
                    )
                    raise PlanRejectedError(
                        "Plan rejected after repair",
                        details={"errors": [e.model_dump() for e in compile_result.errors]},
                    )

            plan = compile_result.plan
            assert plan is not None
            (run_dir / "output" / "compiler-report.json").write_text(
                json.dumps({"ok": True, "notes": plan.compiler_notes}, indent=2),
                encoding="utf-8",
            )
            recorder.emit(
                run_id=run_id,
                event_type="plan.compiled",
                summary="Plan compiled",
                payload={
                    "task_count": len(plan.tasks),
                    "task_order": list(plan.task_order),
                    "notes": plan.compiler_notes,
                },
            )

            self._raise_if_cancelled(run_id)

            # Execute
            manifest = self._execute(
                execution_context=execution_context,
                request=request,
                plan=plan,
                usage=usage,
                worktrees=worktrees,
                original_repo=original_repo,
                base_commit=base_commit or "",
                workflow_pack=workflow_pack,
                land_map=land_map,
            )
            return manifest
        except RunCancelledError as exc:
            existing_row = self.db.get_run(run_id)
            last_usage = (
                json.loads(existing_row["usage_json"])
                if existing_row and existing_row.get("usage_json")
                else None
            )
            try:
                recorder.emit(
                    run_id=run_id,
                    event_type="run.cancelled",
                    severity=EventSeverity.WARNING,
                    summary=str(exc),
                    payload={"status": "cancelled"},
                )
            except Exception:
                events.emit(run_id, "run.cancelled", {"error": str(exc)})
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="cancelled",
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                usage=last_usage,
                active_operation=None,
            )
            raise
        except (
            PlanRejectedError,
            BudgetExhaustedError,
            ApprovalBlockedError,
            SkillGrantViolation,
            ToolAuthorizationError,
        ) as exc:
            terminal_status = {
                BudgetExhaustedError: "budget_exhausted",
                PlanRejectedError: "plan_rejected",
                ApprovalBlockedError: "awaiting_approval",
                SkillGrantViolation: "blocked",
                ToolAuthorizationError: "blocked",
            }.get(type(exc), "failed")
            try:
                recorder.emit(
                    run_id=run_id,
                    event_type="run.failed",
                    severity=EventSeverity.ERROR,
                    summary=str(exc),
                    payload={"error": str(exc), "status": terminal_status},
                )
            except Exception:
                events.emit(run_id, "run.failed", {"error": str(exc)})
            details = getattr(exc, "details", None)
            budget_snapshot = details.get("ledger") if isinstance(details, dict) else None
            # `_execute` persists usage after every task; reload it so this
            # terminal-status write doesn't clobber accumulated usage with `{}`.
            existing_row = self.db.get_run(run_id)
            last_usage = (
                json.loads(existing_row["usage_json"])
                if existing_row and existing_row.get("usage_json")
                else None
            )
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status=terminal_status,
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                usage=last_usage,
                budget_snapshot=budget_snapshot,
                active_operation=None,
            )
            raise
        except Exception as exc:
            try:
                recorder.emit(
                    run_id=run_id,
                    event_type="run.failed",
                    severity=EventSeverity.ERROR,
                    summary=str(exc),
                    payload={"error": str(exc)},
                )
            except Exception:
                events.emit(run_id, "run.failed", {"error": str(exc)})
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="failed",
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                active_operation=None,
            )
            raise RuntimeFailureError(str(exc)) from exc
        finally:
            if worktrees is not None:
                # Keep failed worktrees for inspection; cleanup only empty ones later.
                pass

    def resume(self, run_id: str) -> RunManifest:
        """Resume an interrupted run from persisted SQLite/run-dir state (P1.B).

        Rebuilds the live plan by recompiling the persisted planner proposal
        and re-attaching any dynamically-created repair tasks from the task
        table (inserted in dependency-consistent order — see
        `Database.list_tasks_in_creation_order`); skips already-completed
        (success/skipped) tasks so they incur no new model/tool spend;
        reattaches worktrees left on disk; restores the budget ledger from
        its last snapshot so cumulative usage carries over; and retries a
        task that crashed mid-execution (persisted as `running`) once before
        giving up on it.
        """
        run_row = self.db.get_run(run_id)
        if run_row is None:
            raise ConfigurationError(f"Unknown run: {run_id}")
        if run_row["status"] in {"completed", "awaiting_approval"}:
            raise ConfigurationError(
                f"Run {run_id} is already {run_row['status']!r}; nothing to resume"
            )
        request = RunRequest.model_validate(json.loads(run_row["request_json"]))
        base_commit: str | None = run_row.get("base_commit") or None
        run_dir = self.pf_root / "runs" / run_id
        if not run_dir.exists():
            raise ConfigurationError(f"Run directory missing for {run_id}: {run_dir}")
        for sub in (
            "input",
            "worktrees",
            "scratch",
            "artifacts",
            "findings",
            "prompts",
            "output",
            "content",
        ):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)

        budget_snapshot = json.loads(run_row["budget_json"]) if run_row.get("budget_json") else None
        execution_context = self._build_execution_context(
            run_id=run_id,
            request=request,
            run_dir=run_dir,
            budget_snapshot=budget_snapshot,
        )
        artifacts = execution_context.artifacts
        recorder = execution_context.recorder
        ledger = execution_context.ledger
        workflow_pack: WorkflowPack | None = None
        land_map = ArtifactLandMap()
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            validate_pack_handoffs(request, workflow_pack)
            if request.handoff_refs:
                # SD0.B temporary: remove when RunLifecycleEngine owns handoff
                # resolution (issue: remove-coordinator-handoff-resolve-2026-08).
                from product_factory.trust.handoffs import HandoffService

                HandoffService(self.db, self.pf_root).resolve_refs(
                    request,
                    workflow_pack,
                    consumer_run_id=run_id,
                    materialize_dir=run_dir / "input",
                )
            validate_pack_input(workflow_pack, request.pack_input)
            land_map = land_map_for_request(request)
        execution_context = execution_context.with_pack(workflow_pack)
        source_policy = resolve_request_source_policy(
            request, profiles_root=self.config.root / "profiles"
        )
        SourceLedger.for_run(run_dir).record_seed_urls(
            request.pack_input.get("seed_source_urls") or (),
            policy=source_policy,
            task_id="run-input",
        )

        plan_path = run_dir / "output" / "plan.json"
        if not plan_path.exists():
            raise ConfigurationError(
                f"No persisted plan for run {run_id}; cannot resume before planning completed"
            )
        proposal = PlannerOutput.model_validate(json.loads(plan_path.read_text(encoding="utf-8")))
        compile_result = compile_plan(
            proposal,
            max_tasks=request.budget.max_tasks,
            max_parallel_tasks=request.budget.max_parallel_tasks,
            workflow_pack=workflow_pack,
            skill_registry=self.skills,
            profile_digests=self._profile_digests(request),
        )
        if not compile_result.ok or compile_result.plan is None:
            raise PlanRejectedError(
                "Persisted plan no longer compiles",
                details={"errors": [e.model_dump() for e in compile_result.errors]},
            )
        merged_tasks = dict(compile_result.plan.tasks)
        merged_order = list(compile_result.plan.task_order)

        task_status: dict[str, str] = {}
        results: list[TaskResult] = []
        usage = UsageMetrics()
        patch_text = ""
        architecture_md = ""
        evidence_report_md = ""
        documents_by_role: dict[str, str] = {}
        for row in self.db.list_tasks_in_creation_order(run_id):
            spec = TaskSpec.model_validate(json.loads(row["spec_json"]))
            if spec.id not in merged_tasks:
                # Dynamically-created (e.g. repair) task from the interrupted run.
                merged_tasks[spec.id] = spec
                merged_order.append(spec.id)
            status = str(row["status"])
            if status == "running":
                # Crashed mid-task: retry once, then give up (idempotent — a
                # task already retried once has attempt >= 2 persisted).
                attempt = int(row.get("attempt") or 1)
                if attempt >= 2:
                    status = "failed"
                    self.db.upsert_task(
                        run_id=run_id,
                        task_id=spec.id,
                        capability=spec.capability,
                        status="failed",
                        spec=json.loads(row["spec_json"]),
                        ended_at=datetime.now(UTC).isoformat(),
                        active_operation=None,
                    )
                    results.append(
                        TaskResult(
                            task_id=spec.id,
                            status="failed",
                            summary="interrupted_twice_gave_up",
                        )
                    )
                else:
                    status = "pending"
                    self.db.upsert_task(
                        run_id=run_id,
                        task_id=spec.id,
                        capability=spec.capability,
                        status="pending",
                        spec=json.loads(row["spec_json"]),
                        attempt=attempt + 1,
                        active_operation=None,
                    )
            task_status[spec.id] = status
            if status in {"success", "failed", "skipped"} and row.get("result_json"):
                try:
                    result = TaskResult.model_validate(json.loads(row["result_json"]))
                except Exception:
                    continue
                results.append(result)
                usage = usage.merge(result.usage)
                for art in result.artifact_refs:
                    try:
                        if art.logical_name.endswith(".patch") or art.media_type == "text/x-diff":
                            patch_text = artifacts.get_text(art.sha256)
                        role = land_map.role_for_logical_name(art.logical_name)
                        if role is None:
                            continue
                        if art.media_type == "text/markdown":
                            documents_by_role[role] = artifacts.get_text(art.sha256)
                        if role == ROLE_ARCHITECTURE_DOCUMENT:
                            architecture_md = artifacts.get_text(art.sha256)
                        if role == ROLE_EVIDENCE_REPORT:
                            evidence_report_md = artifacts.get_text(art.sha256)
                    except Exception:
                        continue
        for tid in merged_tasks:
            task_status.setdefault(tid, "pending")
        live_plan = compile_result.plan.model_copy(
            update={"tasks": merged_tasks, "task_order": merged_order}
        )

        original_repo: Path | None = None
        worktrees: WorktreeManager | None = None
        if request.repository_path is not None and base_commit:
            original_repo = request.repository_path.resolve()
            worktrees = WorktreeManager(original_repo, run_dir / "worktrees")
            for tid in merged_order:
                if not worktrees.exists_on_disk(tid):
                    continue
                writable = merged_tasks[tid].capability in {
                    "implementation",
                    "repair",
                    "test_design",
                    "composition",
                }
                try:
                    worktrees.reattach(tid, base_commit=base_commit, writable=writable)
                except KeyError:
                    continue

        recorder.emit(
            run_id=run_id,
            event_type="run.resumed",
            summary="Run resumed",
            payload={
                "completed_tasks": sorted(
                    tid for tid, st in task_status.items() if st in {"success", "skipped"}
                ),
                "pending_tasks": sorted(tid for tid, st in task_status.items() if st == "pending"),
            },
        )
        self.db.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status="executing",
            request=request.model_dump(mode="json"),
            base_commit=base_commit,
            usage=usage.model_dump(mode="json"),
            budget_snapshot=ledger.snapshot(),
            active_operation="resuming",
        )

        try:
            return self._execute(
                execution_context=execution_context,
                request=request,
                plan=live_plan,
                usage=usage,
                worktrees=worktrees,
                original_repo=original_repo,
                base_commit=base_commit or "",
                workflow_pack=workflow_pack,
                land_map=land_map,
                initial_task_status=task_status,
                initial_results=results,
                initial_patch_text=patch_text,
                initial_architecture_md=architecture_md,
                initial_evidence_report_md=evidence_report_md,
                initial_documents_by_role=documents_by_role,
            )
        except RunCancelledError as exc:
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="cancelled",
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                active_operation=None,
            )
            recorder.emit(
                run_id=run_id,
                event_type="run.cancelled",
                severity=EventSeverity.WARNING,
                summary=str(exc),
                payload={"status": "cancelled"},
            )
            raise
        except (
            PlanRejectedError,
            BudgetExhaustedError,
            ApprovalBlockedError,
            SkillGrantViolation,
            ToolAuthorizationError,
        ) as exc:
            terminal_status = {
                BudgetExhaustedError: "budget_exhausted",
                PlanRejectedError: "plan_rejected",
                ApprovalBlockedError: "awaiting_approval",
                SkillGrantViolation: "blocked",
                ToolAuthorizationError: "blocked",
            }.get(type(exc), "failed")
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status=terminal_status,
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                active_operation=None,
            )
            raise
        except Exception as exc:
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="failed",
                request=request.model_dump(mode="json"),
                base_commit=base_commit,
                active_operation=None,
            )
            raise RuntimeFailureError(str(exc)) from exc

    def _plan(
        self,
        run_id: str,
        request: RunRequest,
        repo_summary: dict[str, Any] | None,
        repair_errors: list[dict[str, Any]] | None = None,
        *,
        execution_context: RunExecutionContext | None = None,
    ) -> PlannerOutput:
        planner_mode = str(request.metadata.get("planner_mode") or "").strip().lower()
        force_fixed = planner_mode in {"fixed", "complexity_sensitive", "deterministic"}
        force_live = planner_mode == "live"
        use_deterministic = force_fixed or (self.use_deterministic_planner and not force_live)
        if use_deterministic:
            if is_registered_workflow(request.workflow_type):
                proposal = handler_for(request.workflow_type).plan_template(request.request_text)
            else:
                proposal = default_code_change_plan(request.request_text)
        else:
            proposal = plan_with_gateway(
                execution_context.gateway if execution_context is not None else self._raw_gateway,
                run_id=run_id,
                request_text=request.request_text,
                workflow_type=request.workflow_type,
                repository_summary=repo_summary,
                budget=request.budget.model_dump(mode="json"),
                repair_errors=repair_errors,
                allowed_capabilities=(
                    resolve_workflow_pack(request.workflow_type).allowed_capabilities
                    if is_registered_workflow(request.workflow_type)
                    else None
                ),
                seed=(
                    int(request.metadata["benchmark_seed"])
                    if request.metadata.get("benchmark_seed") is not None
                    else None
                ),
            )
            if is_registered_workflow(request.workflow_type):
                pack = resolve_workflow_pack(request.workflow_type)
                allowed = pack.allowed_capabilities
                filtered = [t for t in proposal.tasks if t.capability in allowed]
                if filtered:
                    kept = {t.id for t in filtered}
                    filtered = [
                        t.model_copy(
                            update={
                                "dependencies": [d for d in t.dependencies if d in kept],
                            }
                        )
                        for t in filtered
                    ]
                    finals = [fa for fa in proposal.final_artifacts if fa.composer_task_id in kept]
                    proposal = proposal.model_copy(
                        update={
                            "tasks": filtered,
                            "final_artifacts": finals or proposal.final_artifacts,
                        }
                    )
        proposal = _clamp_proposal_budgets(proposal, self.config)
        if not self.finalizer.pack_declares_patch_output(
            resolve_workflow_pack(request.workflow_type)
            if is_registered_workflow(request.workflow_type)
            else None
        ):
            return proposal
        disable_review = request.metadata.get("disable_review") == "true"
        force_review = request.metadata.get("force_review") == "true"
        disable_analysis = request.metadata.get("disable_analysis") == "true"
        tasks = list(proposal.tasks)
        review_ids = {task.id for task in tasks if task.capability == "independent_review"}
        analysis_ids = {task.id for task in tasks if task.capability == "repository_analysis"}
        if disable_analysis and analysis_ids:
            tasks = [
                task.model_copy(
                    update={
                        "dependencies": [
                            dep for dep in task.dependencies if dep not in analysis_ids
                        ]
                    }
                )
                for task in tasks
                if task.id not in analysis_ids
            ]
        if disable_review and review_ids:
            tasks = [
                task.model_copy(
                    update={
                        "dependencies": [dep for dep in task.dependencies if dep not in review_ids]
                    }
                )
                for task in tasks
                if task.id not in review_ids
            ]
        elif force_review and not review_ids:
            implementation = next(
                (task for task in tasks if task.capability in {"implementation", "repair"}),
                None,
            )
            composition_index = next(
                (i for i, task in enumerate(tasks) if task.capability == "composition"),
                len(tasks),
            )
            if implementation is not None:
                review = TaskSpec(
                    id="POLICY-REVIEW",
                    title="Independent review",
                    capability="independent_review",
                    objective="Review the proposed patch with evidence",
                    dependencies=[implementation.id],
                    expected_output_schema="review_findings.v1",
                    required_tool_classes={"repository_read", "git_read"},
                    acceptance_criteria=[
                        AcceptanceCriterion(
                            id="POLICY-REVIEW-AC1",
                            description="Findings cite file or patch evidence",
                            verification="evidence_check",
                        )
                    ],
                )
                tasks.insert(composition_index, review)
                tasks = [
                    task.model_copy(
                        update={
                            "dependencies": [*task.dependencies, review.id]
                            if task.capability == "composition"
                            else task.dependencies
                        }
                    )
                    for task in tasks
                ]
        proposal = proposal.model_copy(update={"tasks": tasks})
        return self._apply_expected_file_guidance(proposal, request)

    def _apply_expected_file_guidance(
        self, proposal: PlannerOutput, request: RunRequest
    ) -> PlannerOutput:
        expected = [
            path.strip()
            for path in str(request.metadata.get("expected_files") or "").split(",")
            if path.strip()
        ]
        if not expected:
            return proposal
        guidance = (
            "Required deliverable paths (create or modify exactly these paths):\n"
            + "\n".join(f"- {path}" for path in expected)
        )
        tasks: list[TaskSpec] = []
        for task in proposal.tasks:
            if task.capability not in {"implementation", "repair"}:
                tasks.append(task)
                continue
            objective = task.objective
            if "Required deliverable paths" not in objective:
                objective = f"{objective.rstrip()}\n\n{guidance}"
            tasks.append(task.model_copy(update={"objective": objective}))
        return proposal.model_copy(update={"tasks": tasks})

    def _execute(
        self,
        *,
        execution_context: RunExecutionContext,
        request: RunRequest,
        plan: CompiledPlan,
        usage: UsageMetrics,
        worktrees: WorktreeManager | None,
        original_repo: Path | None,
        base_commit: str,
        workflow_pack: WorkflowPack | None = None,
        land_map: ArtifactLandMap | None = None,
        initial_task_status: dict[str, str] | None = None,
        initial_results: list[TaskResult] | None = None,
        initial_patch_text: str = "",
        initial_architecture_md: str = "",
        initial_evidence_report_md: str = "",
        initial_documents_by_role: dict[str, str] | None = None,
    ) -> RunManifest:
        run_id = execution_context.run_id
        run_dir = execution_context.run_dir
        artifacts = execution_context.artifacts
        recorder = execution_context.recorder
        ledger = execution_context.ledger
        # `initial_*` are only populated by `resume()` (P1.B): they seed the
        # wave loop with already-completed task state so resumed runs incur
        # no new model/tool spend for success/skipped tasks.
        land_map = land_map or ArtifactLandMap()
        architecture_name = land_map.logical_name_for(
            ROLE_ARCHITECTURE_DOCUMENT, default="ARCHITECTURE.md"
        )
        patch_name = land_map.logical_name_for(ROLE_PROPOSED_PATCH, default="proposed.patch")
        # A composer task owns exactly one deliverable role, so a pack can declare
        # several documents without the coordinator branching on workflow type to
        # decide what each composition task should produce.
        composer_roles = {
            spec.composer_task_id: spec.role for spec in plan.final_artifacts if spec.role
        }
        documents_by_role: dict[str, str] = dict(initial_documents_by_role or {})
        # A quality pack's findings *are* its product: a blocking finding must not
        # spawn a repair task or fail the run the way it does for a code change.
        findings_are_deliverable = bool(
            workflow_pack is not None and workflow_pack.execution_policy.findings_are_deliverable
        )
        task_status = initial_task_status or {tid: "pending" for tid in plan.tasks}
        results: list[TaskResult] = list(initial_results or [])
        findings: list[Finding] = [f for r in results for f in r.findings]
        # Repair tasks are always named "R-{idx:03d}" (see `orchestration/repair.py`);
        # recount them so a resumed run's manifest reports an accurate total.
        repair_count = sum(1 for tid in plan.tasks if tid.startswith("R-") and tid[2:].isdigit())
        repair_origins: dict[str, str] = {}
        origin_repair_attempts: dict[str, int] = {}
        no_progress_count = 0
        previous_patch_fp: str | None = None
        previous_finding_ids: list[str] = []
        previous_validation_failures: set[str] = set()
        patch_text = initial_patch_text
        architecture_md = initial_architecture_md
        evidence_report_md = initial_evidence_report_md
        validation_results: list[ValidatorResult] = []
        validation_evidence_refs = [
            str(value)
            for value in (request.pack_input.get("validation_evidence_refs") or [])
            if str(value).strip()
        ]
        collected_validator_results: list[dict[str, Any]] = []

        self.db.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status="executing",
            request=request.model_dump(mode="json"),
            base_commit=base_commit or None,
            active_operation="executing",
            usage=usage.model_dump(mode="json"),
        )
        recorder.emit(
            run_id=run_id,
            event_type="run.status_changed",
            summary="Executing",
            payload={"status": "executing"},
        )

        spent = Decimal("0")
        # Dynamic task dict so repairs can be added
        live_plan = plan

        while True:
            execution_context.cancel_check()
            if spent >= request.budget.max_cost_usd:
                raise BudgetExhaustedError("Run budget exhausted")
            ledger.check_wall_clock()

            ready = self.wave_scheduler.select_ready(
                live_plan, task_status, max_parallel=request.budget.max_parallel_tasks
            )
            if not ready:
                if all(task_status[t] in {"success", "failed", "skipped"} for t in live_plan.tasks):
                    break
                # deadlock
                pending = [t for t, s in task_status.items() if s == "pending"]
                if pending:
                    failed = [
                        f"{result.task_id}: {result.summary}"
                        for result in results
                        if result.status not in {"success", "partial"}
                    ]
                    if failed:
                        raise RuntimeFailureError("Dependency failed; " + "; ".join(failed))
                    raise RuntimeFailureError(f"Unsatisfiable dependencies for tasks: {pending}")
                break

            # Execute wave: read-only tasks and predicted-disjoint writers run
            # concurrently (bounded by max_parallel_tasks via `run_wave`);
            # conflicting writers are serialized. Same-wave tasks never depend
            # on each other (enforced by `runnable_tasks`), so dependency
            # context is safely precomputed from the pre-wave `results`
            # snapshot. Result processing below stays single-threaded and
            # iterates `ready` (plan order) regardless of completion order,
            # giving a deterministic merge order (P1.F).
            if spent >= request.budget.max_cost_usd:
                raise BudgetExhaustedError("Run budget exhausted before wave")
            ledger.check_wall_clock()
            pre_wave_results = list(results)
            dependency_outputs_by_task = {
                task.id: [
                    {
                        "task_id": prior.task_id,
                        "dependencies": live_plan.tasks[prior.task_id].dependencies,
                        "summary": prior.summary,
                        "artifact_refs": [
                            ref.model_dump(mode="json") for ref in prior.artifact_refs
                        ],
                        "artifact_excerpts": [
                            {
                                "logical_name": ref.logical_name,
                                "sha256": ref.sha256,
                                "content": artifacts.get_text(ref.sha256)[:12_000],
                            }
                            for ref in prior.artifact_refs
                            if ref.media_type.startswith("text/")
                            or ref.media_type == "application/json"
                        ],
                        "findings": [finding.model_dump(mode="json") for finding in prior.findings],
                    }
                    for prior in pre_wave_results
                    if prior.task_id
                    in self.wave_scheduler.transitive_dependencies(live_plan, task.id)
                ]
                for task in ready
            }
            for task in ready:
                task_status[task.id] = "running"
                self.db.upsert_task(
                    run_id=run_id,
                    task_id=task.id,
                    capability=task.capability,
                    status="running",
                    spec=task.model_dump(mode="json"),
                    started_at=datetime.now(UTC).isoformat(),
                    active_operation=task.capability,
                )
                recorder.emit(
                    run_id=run_id,
                    event_type="task.started",
                    task_id=task.id,
                    summary=f"Task {task.id} started",
                    payload={
                        "task_id": task.id,
                        "capability": task.capability,
                        "title": task.title,
                    },
                )

            def _run_one(
                task: TaskSpec,
                # Bound per wave so the closure reads this wave's precomputed
                # dependency context, never a later wave's.
                dependency_outputs_by_task: dict[
                    str, list[dict[str, Any]]
                ] = dependency_outputs_by_task,
                validation_evidence_refs: list[str] = validation_evidence_refs,
                collected_validator_results: list[dict[str, Any]] = collected_validator_results,
            ) -> TaskResult:
                return self._execute_task(
                    execution_context=execution_context,
                    request=request,
                    task=task,
                    worktrees=worktrees,
                    original_repo=original_repo,
                    base_commit=base_commit,
                    dependency_outputs=dependency_outputs_by_task[task.id],
                    land_map=land_map,
                    composer_role=composer_roles.get(task.id),
                    validation_evidence_refs=validation_evidence_refs,
                    validator_results=collected_validator_results,
                )

            wave_results: list[TaskResult] = run_wave(
                ready,
                executor_fn=_run_one,
                max_workers=request.budget.max_parallel_tasks,
            )
            execution_context.cancel_check()

            for task, result in zip(ready, wave_results, strict=True):
                usage = usage.merge(result.usage)
                spent = usage.estimated_cost_usd
                task_status[task.id] = "success" if result.status == "success" else result.status
                results.append(result)
                findings.extend(result.findings)
                recorder.emit(
                    run_id=run_id,
                    event_type="task.completed" if result.status == "success" else "task.failed",
                    task_id=task.id,
                    summary=f"Task {task.id} {result.status}",
                    payload={
                        "task_id": task.id,
                        "status": result.status,
                        "summary": result.summary,
                        "usage": result.usage.model_dump(mode="json"),
                    },
                    severity=EventSeverity.INFO
                    if result.status == "success"
                    else EventSeverity.ERROR,
                )
                recorder.emit(
                    run_id=run_id,
                    event_type="budget.updated",
                    summary="Budget progress",
                    payload={
                        "spent_usd": str(spent),
                        "max_cost_usd": str(request.budget.max_cost_usd),
                    },
                )
                self.db.upsert_run(
                    run_id=run_id,
                    workflow_type=request.workflow_type,
                    status="executing",
                    request=request.model_dump(mode="json"),
                    base_commit=base_commit or None,
                    usage=usage.model_dump(mode="json"),
                    budget_snapshot=ledger.snapshot(),
                    active_operation=f"task:{task.id}",
                )
                if result.changed_files and "patch" in (result.summary.lower()):
                    pass
                for art in result.artifact_refs:
                    if art.logical_name.endswith(".patch") or art.media_type == "text/x-diff":
                        patch_text = artifacts.get_text(art.sha256)
                    role = land_map.role_for_logical_name(art.logical_name)
                    if role is None:
                        continue
                    if art.media_type in {"text/markdown", "application/json"}:
                        documents_by_role[role] = artifacts.get_text(art.sha256)
                    if role == ROLE_ARCHITECTURE_DOCUMENT:
                        architecture_md = artifacts.get_text(art.sha256)
                    if role == ROLE_EVIDENCE_REPORT:
                        evidence_report_md = artifacts.get_text(art.sha256)

            # After wave: deterministic validation for implementation outputs
            for result in wave_results:
                if live_plan.tasks[result.task_id].capability in {
                    "implementation",
                    "repair",
                    "composition",
                    "independent_review",
                }:
                    validation_results = self.validation_repair.validate_outputs(
                        request=request,
                        patch_text=patch_text,
                        architecture_md=architecture_md,
                        evidence_report_md=evidence_report_md,
                        original_repo=original_repo,
                        task=live_plan.tasks[result.task_id],
                        findings=result.findings,
                        ledger=ledger,
                        artifact_store=artifacts,
                        input_revision=base_commit or "worktree",
                        workflow_pack=workflow_pack,
                    )
                    collected_validator_results.extend(
                        value.model_dump(mode="json") for value in validation_results
                    )
                    validation_evidence_refs.extend(
                        str(value.details["validation_evidence_ref"])
                        for value in validation_results
                        if value.details.get("validation_evidence_ref")
                    )
                    validation_evidence_refs = list(dict.fromkeys(validation_evidence_refs))
                    (run_dir / "output" / "validation-report.json").write_text(
                        json.dumps(
                            [v.model_dump(mode="json") for v in validation_results], indent=2
                        ),
                        encoding="utf-8",
                    )
                    validation_artifact = artifacts.put_json(
                        [v.model_dump(mode="json") for v in validation_results],
                        logical_name=f"validation-{result.task_id}.json",
                        created_by_task_id=result.task_id,
                    )
                    self.db.record_validator_results(
                        run_id=run_id,
                        task_id=result.task_id,
                        results=[v.model_dump(mode="json") for v in validation_results],
                    )
                    recorder.emit(
                        run_id=run_id,
                        event_type="validation.completed",
                        task_id=result.task_id,
                        summary="Validation completed",
                        payload={
                            "results": [v.model_dump(mode="json") for v in validation_results],
                            "blocking": has_blocking_failures(validation_results),
                            "patch_fingerprint": (
                                patch_fingerprint(patch_text) if patch_text else None
                            ),
                            "artifact_sha256": validation_artifact.sha256,
                        },
                    )
                    blocking_findings = [
                        finding
                        for finding in findings
                        if finding.status == "open" and finding.severity == "blocking"
                    ]
                    if findings_are_deliverable:
                        # Reporting packs surface defects for a human to act on;
                        # they hold no write grants and cannot repair anything.
                        blocking_findings = []
                    if live_plan.tasks[
                        result.task_id
                    ].capability == "repair" and not has_blocking_failures(validation_results):
                        origin = repair_origins.get(result.task_id)
                        if origin is not None and task_status.get(origin) == "failed":
                            task_status[origin] = "skipped"
                        for finding in blocking_findings:
                            finding.status = "resolved"
                        blocking_findings = []
                    if (
                        request.metadata.get("disable_validation_repair") != "true"
                        and (
                            workflow_pack is None
                            or live_plan.tasks[result.task_id].capability
                            in workflow_pack.execution_policy.repair_eligible_capabilities
                        )
                        and (
                            result.status != "success"
                            or has_blocking_failures(validation_results)
                            or blocking_findings
                        )
                        and repair_count < (request.budget.max_total_repair_tasks)
                    ):
                        origin_task = live_plan.tasks[result.task_id]
                        attempts = origin_repair_attempts.get(result.task_id, 0)
                        if attempts >= origin_task.budget.max_repair_attempts:
                            recorder.emit(
                                run_id=run_id,
                                event_type="repair.budget_exhausted",
                                task_id=result.task_id,
                                severity=EventSeverity.WARNING,
                                summary="Per-task repair budget exhausted",
                                payload={
                                    "attempts": attempts,
                                    "max_repair_attempts": (origin_task.budget.max_repair_attempts),
                                },
                            )
                        else:
                            repair_failures = list(validation_results)
                            if result.status != "success":
                                repair_failures.append(
                                    ValidatorResult(
                                        validator_id="task_execution",
                                        status="fail",
                                        message=result.summary,
                                        details={"task_status": result.status},
                                    )
                                )
                            repairs = create_repair_tasks(
                                failures=repair_failures,
                                findings=[f for f in findings if f.status == "open"],
                                originating_task_id=result.task_id,
                                allowed_path_patterns=live_plan.tasks[
                                    result.task_id
                                ].allowed_path_patterns,
                                next_id_start=repair_count + 1,
                                registered_command_ids=resolve_validation_command_ids(request)
                                or list(self.config.policies.registered_commands),
                            )
                            repair_limit = (
                                1 if result.status != "success" else request.budget.max_task_repairs
                            )
                            created_any = False
                            for rt in repairs[:repair_limit]:
                                if result.status != "success":
                                    rt = rt.model_copy(
                                        update={
                                            "dependencies": list(
                                                live_plan.tasks[result.task_id].dependencies
                                            )
                                        }
                                    )
                                # extend plan
                                new_tasks = dict(live_plan.tasks)
                                new_tasks[rt.id] = rt
                                repair_origins[rt.id] = result.task_id
                                for downstream_id, downstream in list(new_tasks.items()):
                                    if (
                                        downstream_id != rt.id
                                        and task_status.get(downstream_id) == "pending"
                                        and result.task_id in downstream.dependencies
                                        and rt.id not in downstream.dependencies
                                    ):
                                        new_tasks[downstream_id] = downstream.model_copy(
                                            update={
                                                "dependencies": (
                                                    [
                                                        rt.id if dep == result.task_id else dep
                                                        for dep in downstream.dependencies
                                                    ]
                                                    if result.status != "success"
                                                    else [*downstream.dependencies, rt.id]
                                                )
                                            }
                                        )
                                new_order = list(live_plan.task_order) + [rt.id]
                                live_plan = live_plan.model_copy(
                                    update={"tasks": new_tasks, "task_order": new_order}
                                )
                                task_status[rt.id] = "pending"
                                repair_count += 1
                                created_any = True
                            if created_any:
                                origin_repair_attempts[result.task_id] = attempts + 1
                        fp = patch_fingerprint(patch_text) if patch_text else None
                        current_validation_failures = {
                            v.validator_id
                            for v in validation_results
                            if v.status in {"fail", "error"}
                        }
                        criterion_improved = bool(previous_validation_failures) and (
                            current_validation_failures < previous_validation_failures
                        )
                        no_progress_count, _ = update_no_progress(
                            no_progress_count=no_progress_count,
                            previous_findings=previous_finding_ids,
                            current_findings=[f.id for f in findings if f.severity == "blocking"],
                            previous_patch_fp=previous_patch_fp,
                            current_patch_fp=fp,
                            criterion_improved=criterion_improved,
                        )
                        previous_patch_fp = fp
                        previous_finding_ids = [f.id for f in findings if f.severity == "blocking"]
                        previous_validation_failures = current_validation_failures
                        if should_terminate_no_progress(no_progress_count):
                            recorder.emit(
                                run_id=run_id,
                                event_type="run.no_progress",
                                severity=EventSeverity.WARNING,
                                summary="No progress",
                                payload={"count": no_progress_count},
                            )
                            break

            if should_terminate_no_progress(no_progress_count):
                break

        # Final artifacts
        if self.finalizer.pack_declares_patch_output(workflow_pack):
            if not patch_text and worktrees is not None and original_repo is not None:
                # Try collect from last implementation worktree
                for tid, _st in task_status.items():
                    if live_plan.tasks[tid].capability in {
                        "implementation",
                        "repair",
                        "composition",
                    }:
                        try:
                            wt = worktrees.get(tid)
                            patch_text = create_patch(wt.path, base_commit)
                        except Exception:
                            continue
            if patch_text:
                (run_dir / "output" / patch_name).write_text(patch_text, encoding="utf-8")
                artifacts.put_text(
                    patch_text,
                    media_type="text/x-diff",
                    logical_name=patch_name,
                    created_by_task_id="compose",
                )
            change_set_entry = land_map.by_role(ROLE_CHANGE_SET)
            change_set = documents_by_role.get(ROLE_CHANGE_SET, "")
            if change_set_entry is not None and change_set.strip():
                (run_dir / "output" / change_set_entry.logical_name).write_text(
                    change_set,
                    encoding="utf-8",
                )
                validation_results.append(
                    validate_json_contract(
                        change_set,
                        schema_id="change_set.v1",
                        validator_id="change_set_contract",
                    )
                )
            elif change_set_entry is not None and change_set_entry.required:
                validation_results.append(
                    ValidatorResult(
                        validator_id="change_set_contract",
                        status="fail",
                        message="Required deliverable change_set was not produced",
                        details={"logical_name": change_set_entry.logical_name},
                    )
                )
        elif self.finalizer.pack_declares_architecture_output(workflow_pack):
            if not architecture_md:
                architecture_md = self.composition.compose_architecture(
                    request.request_text, findings, document_name=architecture_name
                )
            (run_dir / "output" / architecture_name).write_text(architecture_md, encoding="utf-8")
            validation_results.append(validate_architecture_document(architecture_md))
            must_cover = [
                item.strip()
                for item in str(request.metadata.get("must_cover") or "").split("|")
                if item.strip()
            ]
            if must_cover or not isinstance(self._raw_gateway, MockGateway):
                validation_results.extend(
                    validate_architecture_request_specificity(
                        architecture_md,
                        must_cover=must_cover or None,
                        reject_boilerplate=not isinstance(self._raw_gateway, MockGateway),
                    )
                )
            if not isinstance(self._raw_gateway, MockGateway):
                web_check = validate_web_search_used(
                    expected=request_expects_web_citations(request.request_text, request.metadata),
                    connector_enabled=self.config.connectors.is_enabled(TAVILY_CONNECTOR_ID),
                    invocation_count=self._count_connector_invocations(
                        run_id, connector_id=TAVILY_CONNECTOR_ID
                    ),
                )
                if web_check is not None:
                    validation_results.append(web_check)
        elif is_registered_workflow(request.workflow_type):
            # Role-driven final validation for investigation, quality_gate,
            # discovery, and any future document pack — never the architecture path.
            pack_handler = handler_for(request.workflow_type)
            source_policy = resolve_request_source_policy(
                request, profiles_root=self.config.root / "profiles"
            )
            for entry in land_map.entries:
                document = documents_by_role.get(entry.role, "")
                if entry.role == ROLE_EVIDENCE_REPORT and evidence_report_md.strip():
                    document = evidence_report_md
                if not document.strip():
                    if entry.required:
                        # Investigation/discovery keep a deterministic compose
                        # fallback (mock E2E). Quality gate and similar packs
                        # fail closed when a required deliverable is absent.
                        if (
                            workflow_pack is not None
                            and entry.role
                            in workflow_pack.execution_policy.fallback_composition_roles
                        ):
                            try:
                                document = pack_handler.compose(
                                    entry.role,
                                    ComposeContext(
                                        composition=self.composition,
                                        request=request,
                                        role=entry.role,
                                        document_name=entry.logical_name,
                                        findings=findings,
                                        dependency_outputs=[],
                                        use_mock=isinstance(self._raw_gateway, MockGateway),
                                        compose_architecture=self.composition.compose_architecture,
                                        compose_evidence_report=self.composition.compose_evidence_report,
                                        compose_feasibility_dossier=(
                                            self.composition.compose_feasibility_dossier
                                        ),
                                        compose_change_intake=self.composition.compose_change_intake,
                                        compose_quality_document=self.composition.compose_quality_document,
                                        validation_evidence_refs=validation_evidence_refs,
                                        validator_results=collected_validator_results,
                                    ),
                                )
                                documents_by_role[entry.role] = document
                                if entry.role == ROLE_EVIDENCE_REPORT:
                                    evidence_report_md = document
                            except Exception:
                                validation_results.append(
                                    ValidatorResult(
                                        validator_id=pack_handler.validator_id(entry.role),
                                        status="fail",
                                        message=(
                                            f"Required deliverable {entry.role} was not produced"
                                        ),
                                        details={"logical_name": entry.logical_name},
                                    )
                                )
                                continue
                        else:
                            validation_results.append(
                                ValidatorResult(
                                    validator_id=pack_handler.validator_id(entry.role),
                                    status="fail",
                                    message=(f"Required deliverable {entry.role} was not produced"),
                                    details={"logical_name": entry.logical_name},
                                )
                            )
                            continue
                    else:
                        continue
                (run_dir / "output" / entry.logical_name).write_text(document, encoding="utf-8")
                if entry.role == ROLE_VERIFICATION_REPORT:
                    validation_results.append(validate_verification_report(document))
                else:
                    role_validator_id = pack_handler.validator_id(entry.role)
                    if role_validator_id == "release_plan_contract":
                        validation_results.append(validate_release_plan(document))
                    elif role_validator_id == "deployment_record_contract":
                        validation_results.append(validate_deployment_record(document))
                    elif role_validator_id == "operational_record_contract":
                        validation_results.append(validate_operational_record(document))
                    else:
                        validation_results.append(
                            validate_document_sections(
                                document,
                                validator_id=role_validator_id,
                                required_sections=pack_handler.required_sections(entry.role),
                            )
                        )
                    validation_results.append(validate_secrets(document))
                policy_validators = set(
                    workflow_pack.execution_policy.validators if workflow_pack is not None else ()
                )
                if "citation_presence" in policy_validators and entry.role == ROLE_EVIDENCE_REPORT:
                    validation_results.append(validate_citations(document))
                if (
                    "research_provenance" in policy_validators
                    and entry.role == ROLE_FEASIBILITY_DOSSIER
                ):
                    validation_results.append(validate_research_provenance(document))
                    validation_results.append(validate_option_comparison(document))
                    validation_results.append(
                        validate_regulated_claims(document, policy=source_policy)
                    )
                    validation_results.append(validate_recommendation(document))
                if (
                    "intake_sections" in policy_validators
                    and entry.role in {ROLE_CHANGE_BRIEF, ROLE_CLARIFICATION_REQUEST}
                    and document.strip()
                ):
                    # Role-specific section validator (overrides the generic pass above
                    # only when the primary landable is present).
                    validation_results.append(validate_intake_sections(document, role=entry.role))
                    validation_results.append(
                        validate_intake_no_invention(
                            document,
                            role=entry.role,
                            request_text=request.request_text,
                            pack_input=getattr(request, "pack_input", None) or {},
                        )
                    )
            if (
                workflow_pack is not None
                and "citation_presence" in workflow_pack.execution_policy.validators
            ):
                findings_doc = documents_by_role.get(ROLE_QUALITY_FINDINGS, "")
                if findings_doc.strip():
                    validation_results.append(validate_citations(findings_doc))
        else:
            if not architecture_md:
                architecture_md = self.composition.compose_architecture(
                    request.request_text, findings, document_name=architecture_name
                )
            (run_dir / "output" / architecture_name).write_text(architecture_md, encoding="utf-8")
            validation_results.append(validate_architecture_document(architecture_md))
            must_cover = [
                item.strip()
                for item in str(request.metadata.get("must_cover") or "").split("|")
                if item.strip()
            ]
            if must_cover or not isinstance(self._raw_gateway, MockGateway):
                validation_results.extend(
                    validate_architecture_request_specificity(
                        architecture_md,
                        must_cover=must_cover or None,
                        reject_boilerplate=not isinstance(self._raw_gateway, MockGateway),
                    )
                )
            if not isinstance(self._raw_gateway, MockGateway):
                web_check = validate_web_search_used(
                    expected=request_expects_web_citations(request.request_text, request.metadata),
                    connector_enabled=self.config.connectors.is_enabled(TAVILY_CONNECTOR_ID),
                    invocation_count=self._count_connector_invocations(
                        run_id, connector_id=TAVILY_CONNECTOR_ID
                    ),
                )
                if web_check is not None:
                    validation_results.append(web_check)

        # Approval gate for code changes
        final_status: str
        missing_policy_roles = (
            {
                role
                for role in workflow_pack.execution_policy.required_output_roles
                if not str(documents_by_role.get(role) or "").strip()
            }
            if workflow_pack is not None
            else set()
        )
        invalid_exclusive_groups = (
            [
                group
                for group in workflow_pack.execution_policy.exactly_one_output_role_groups
                if sum(1 for role in group if str(documents_by_role.get(role) or "").strip()) != 1
            ]
            if workflow_pack is not None
            else []
        )
        terminal_failure = (
            any(status == "failed" for status in task_status.values())
            or has_blocking_failures(validation_results)
            or bool(missing_policy_roles)
            or bool(invalid_exclusive_groups)
            or (self.finalizer.pack_declares_patch_output(workflow_pack) and not patch_text.strip())
        )
        if terminal_failure:
            final_status = "failed"
        elif (
            self.finalizer.pack_declares_patch_output(workflow_pack)
            and request.approval_policy == "manual_apply"
        ):
            approval = {
                "run_id": run_id,
                "base_commit": base_commit,
                "patch_ref": "output/proposed.patch",
                "changed_files": changed_files_from_patch(patch_text),
                "validation_summary": [v.model_dump(mode="json") for v in validation_results],
                "review_findings": [f.model_dump(mode="json") for f in findings],
                "estimated_cost_usd": str(usage.estimated_cost_usd),
                "actions": ["approve", "reject", "request_revision"],
                "status": "awaiting_approval",
            }
            (run_dir / "output" / "approval.json").write_text(
                json.dumps(approval, indent=2), encoding="utf-8"
            )
            final_status = "awaiting_approval"
            recorder.emit(
                run_id=run_id,
                event_type="approval.required",
                summary="Approval required",
                payload={
                    "run_id": run_id,
                    "base_commit": base_commit,
                    "actions": approval.get("actions"),
                    "status": "awaiting_approval",
                    "estimated_cost_usd": approval.get("estimated_cost_usd"),
                },
            )
        else:
            final_status = "completed"

        (run_dir / "output" / "run-summary.md").write_text(
            f"# Run {run_id}\n\nStatus: {final_status}\n\n"
            f"Tasks: {len(task_status)}\nRepairs: {repair_count}\n"
            f"Cost USD: {usage.estimated_cost_usd}\n",
            encoding="utf-8",
        )
        findings_path = run_dir / "findings"
        for f in findings:
            (findings_path / f"{f.id}.json").write_text(
                f.model_dump_json(indent=2), encoding="utf-8"
            )

        manifest = RunManifest(
            run_id=run_id,
            request=request,
            final_status=final_status,  # type: ignore[arg-type]
            ended_at=datetime.now(UTC),
            base_commit=base_commit or None,
            workspace_provenance=request.workspace_provenance,
            usage=usage,
            artifact_paths={
                p.name: str(p.relative_to(run_dir))
                for p in (run_dir / "output").iterdir()
                if p.is_file()
            },
            findings_count=len(findings),
            task_count=len(task_status),
            repair_count=repair_count,
            notes=[
                f"no_progress_count={no_progress_count}",
                *[
                    f"{result.task_id}:{result.summary}"
                    for result in results
                    if task_status.get(result.task_id) == "failed"
                ],
            ],
            metadata=workflow_pack.manifest_metadata() if workflow_pack is not None else {},
        )
        (run_dir / "run-manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        self.db.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status=final_status,
            request=request.model_dump(mode="json"),
            base_commit=base_commit or None,
            usage=usage.model_dump(mode="json"),
            manifest=manifest.model_dump(mode="json"),
            active_operation=None,
        )
        recorder.emit(
            run_id=run_id,
            event_type="run.finished",
            summary=f"Run {final_status}",
            payload={"status": final_status, "usage": usage.model_dump(mode="json")},
        )
        return manifest

    def _profile_digests(self, request: RunRequest) -> dict[str, str]:
        digests = ProfileRegistry.load(self.config.root / "profiles").digests()
        if request.repository_path is not None:
            stack_profile = discover_stack_profile(
                request.repository_path,
                registered_command_ids=(
                    resolve_validation_command_ids(request)
                    or self.config.policies.registered_commands
                ),
            )
            digests.update(stack_profile.as_manifest_entry())
        source_policy = resolve_request_source_policy(
            request, profiles_root=self.config.root / "profiles"
        )
        if source_policy is not None:
            digests.update(source_policy.as_manifest_entry())
        for pack in resolve_request_domain_packs(request, packs_root=self.config.root / "packs"):
            digests.update(pack.as_manifest_entry())
        for profile in resolve_request_policy_profiles(
            request, profiles_root=self.config.root / "profiles"
        ):
            digests.update(profile.as_manifest_entry())
        return digests

    def _record_artifact_instance(self, instance: ArtifactInstance) -> None:
        self.db.record_artifact_instance(instance.model_dump(mode="json"))

    def _pin_stack_profile(
        self,
        *,
        request: RunRequest,
        artifacts: ArtifactStore,
        run_id: str,
        task_id: str,
        existing_policy: dict[str, Any] | None,
    ) -> tuple[dict[str, str], str | None, str | None, str | None]:
        """Return (profile_digests, digest, artifact_sha256, schema_version).

        On resume, reuse the pinned stack-profile artifact instead of re-detecting.
        """

        digests = ProfileRegistry.load(self.config.root / "profiles").digests()
        source_policy = resolve_request_source_policy(
            request, profiles_root=self.config.root / "profiles"
        )
        if source_policy is not None:
            digests.update(source_policy.as_manifest_entry())
        for pack in resolve_request_domain_packs(request, packs_root=self.config.root / "packs"):
            digests.update(pack.as_manifest_entry())
        for profile in resolve_request_policy_profiles(
            request, profiles_root=self.config.root / "profiles"
        ):
            digests.update(profile.as_manifest_entry())

        stack_digest = None
        stack_sha = None
        stack_version = None
        if existing_policy:
            stack_sha = existing_policy.get("stack_profile_artifact_sha256")
            stack_digest = existing_policy.get("stack_profile_digest")
            stack_version = existing_policy.get("stack_profile_schema_version")
            if stack_sha and artifacts.exists(str(stack_sha)):
                try:
                    pinned = StackProfile.model_validate(
                        json.loads(artifacts.get_text(str(stack_sha)))
                    )
                    digests.update(pinned.as_manifest_entry())
                    return digests, pinned.digest, str(stack_sha), pinned.version
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    if stack_digest:
                        digests["stack:pinned"] = str(stack_digest)
                    return digests, stack_digest, str(stack_sha), stack_version
            if stack_sha:
                return digests, stack_digest, str(stack_sha), stack_version

        existing_instance = next(
            (
                row
                for row in self.db.list_artifact_instances(run_id)
                if row.get("role") == "stack_profile"
            ),
            None,
        )
        if existing_instance and artifacts.exists(str(existing_instance["sha256"])):
            try:
                pinned = StackProfile.model_validate(
                    json.loads(artifacts.get_text(str(existing_instance["sha256"])))
                )
                digests.update(pinned.as_manifest_entry())
                return (
                    digests,
                    pinned.digest,
                    str(existing_instance["sha256"]),
                    pinned.version,
                )
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass

        if request.repository_path is None:
            return digests, None, None, None

        stack_profile = discover_stack_profile(
            request.repository_path,
            registered_command_ids=(
                resolve_validation_command_ids(request) or self.config.policies.registered_commands
            ),
        )
        digests.update(stack_profile.as_manifest_entry())
        ref = artifacts.put_json(
            stack_profile.model_dump(mode="json"),
            logical_name="stack-profile.json",
            created_by_task_id=task_id,
            schema_id="stack_profile.v1",
            schema_version=stack_profile.version,
            trust_level="generated",
        )
        self.db.record_artifact(ref.model_dump(mode="json"))
        self._record_artifact_instance(
            ArtifactInstance.create(
                run_id=run_id,
                sha256=ref.sha256,
                content_class="durable_output",
                capture_level="full",
                role="stack_profile",
                producer_task_id=task_id,
                media_type=ref.media_type,
                schema_id="stack_profile.v1",
                schema_version=stack_profile.version,
                size_bytes=ref.size_bytes,
                display_name="stack-profile.json",
                metadata={"stack_id": stack_profile.id, "digest": stack_profile.digest},
            )
        )
        return digests, stack_profile.digest, ref.sha256, stack_profile.version

    def _research_prompt_tool_names(
        self,
        *,
        task: TaskSpec,
        request: RunRequest,
        allowed: set[str],
    ) -> tuple[list[str] | None, str | None]:
        if task.capability == "interface_analysis":
            interface_tools = {
                "parse_contract",
                "contract_inventory",
                "diff_contracts",
                "map_capabilities",
                "generate_synthetic_fixture",
                "run_contract_simulation",
            }
            return (
                sorted(name for name in allowed if name in interface_tools),
                "interface_agent_loop_tools",
            )
        if task.capability not in _RESEARCH_LOOP_CAPABILITIES:
            return None, None
        loop_tool_names = (
            {
                TOOL_WEB_SEARCH,
                "read_file",
                "list_files",
                "search_text",
            }
            | _SOURCE_READ_TOOL_NAMES
            | _EVIDENCE_BUILD_TOOL_NAMES
        )
        prompt = sorted(name for name in allowed if name in loop_tool_names)
        if set(prompt) == allowed:
            return None, None
        return prompt, "research_agent_loop_tools"

    def _execute_task(
        self,
        *,
        execution_context: RunExecutionContext,
        request: RunRequest,
        task: TaskSpec,
        worktrees: WorktreeManager | None,
        original_repo: Path | None,
        base_commit: str,
        dependency_outputs: list[dict[str, Any]] | None = None,
        land_map: ArtifactLandMap | None = None,
        composer_role: str | None = None,
        validation_evidence_refs: list[str] | None = None,
        validator_results: list[dict[str, Any]] | None = None,
    ) -> TaskResult:
        run_id = execution_context.run_id
        run_dir = execution_context.run_dir
        artifacts = execution_context.artifacts
        recorder = execution_context.recorder
        ledger = execution_context.ledger
        gateway = execution_context.gateway
        land_map = land_map or ArtifactLandMap()
        validation_evidence_refs = validation_evidence_refs or []
        validator_results = validator_results or []
        profile = resolve_task_model_profile(task, metadata=request.metadata)
        agent_profile = agent_profile_for(task.capability)

        skill_policy: dict[str, Any] = {}
        if is_registered_workflow(request.workflow_type):
            skill_policy = dict(resolve_workflow_pack(request.workflow_type).skill_policy)
        skills_disabled = request.metadata.get("disable_skills") == "true"
        if skills_disabled:
            skills = []
        else:
            skills = self.skills.match(
                capability=task.capability,
                required_skills=task.required_skills,
                skill_policy=skill_policy,
            )

        existing_policy_data: dict[str, Any] | None = None
        existing_row = self.db.get_task(run_id, task.id)
        if existing_row and existing_row.get("effective_policy_json"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                loaded = json.loads(existing_row["effective_policy_json"])
                if isinstance(loaded, dict):
                    existing_policy_data = loaded

        profile_digests, stack_digest, stack_sha, stack_version = self._pin_stack_profile(
            request=request,
            artifacts=artifacts,
            run_id=run_id,
            task_id=task.id,
            existing_policy=existing_policy_data,
        )

        profile_cfg = self.config.models.profiles.get(profile)
        route_class = profile_cfg.route_class if profile_cfg is not None else "cloud"
        fallback_cfg = profile_cfg.cloud_fallback if profile_cfg is not None else None
        fallback_model_profile = (
            fallback_cfg.profile if fallback_cfg is not None and fallback_cfg.enabled else None
        )
        fallback_eligible = bool(fallback_cfg is not None and fallback_cfg.enabled)

        pack_id = None
        pack_version = None
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            pack_id = workflow_pack.id
            pack_version = getattr(workflow_pack, "version", None)

        if existing_policy_data:
            effective_policy = EffectiveTaskPolicy.model_validate(existing_policy_data)
        else:
            connector_tool_names = self.connector_registry.tool_names()
            grantable = grantable_connector_names_for_task(
                task=task,
                grantable_fn=self.connector_broker.grantable_tool_names,
            )
            # Precompute allowed set so research prompt reduction can be recorded.
            allowed_preview, _, _ = compute_allowed_tool_names(
                task=task,
                request=request,
                tool_registry=self.tool_registry,
                connector_tool_names=connector_tool_names,
                grantable_connector_tools=grantable,
                web_search_tool=TOOL_WEB_SEARCH,
                denied_tool_names=workflow_pack.execution_policy.denied_tool_names,
                pack_allowed_tool_classes=(workflow_pack.execution_policy.allowed_tool_classes),
            )
            prompt_names, reduction_reason = self._research_prompt_tool_names(
                task=task,
                request=request,
                allowed=allowed_preview,
            )
            domain_packs = resolve_request_domain_packs(
                request, packs_root=self.config.root / "packs"
            )
            policy_profiles = resolve_request_policy_profiles(
                request, profiles_root=self.config.root / "profiles"
            )
            composition_gate = evaluate_composition_gates(
                request=request,
                domain_packs=domain_packs,
                policy_profiles=policy_profiles,
                granted_tool_names=allowed_preview,
                granted_tool_classes={
                    t.tool_class for t in self.tool_registry.list() if t.name in allowed_preview
                },
                skill_ids=[s.manifest.id for s in skills],
            )
            if not composition_gate.ok:
                conflict_result = TaskResult(
                    task_id=task.id,
                    status="failed",
                    summary="composition_conflict",
                    validator_results=[
                        ValidatorResult(
                            validator_id="composition_conflict",
                            status="fail",
                            message="Domain/policy composition conflict detected",
                            details={"conflicts": composition_gate.conflicts},
                        )
                    ],
                    model_profile=profile,
                )
                self.db.upsert_task(
                    run_id=run_id,
                    task_id=task.id,
                    capability=task.capability,
                    status="failed",
                    spec=task.model_dump(mode="json"),
                    result=conflict_result.model_dump(mode="json"),
                    ended_at=datetime.now(UTC).isoformat(),
                    active_operation=None,
                )
                return conflict_result
            profile_digests.update(composition_gate.profile_digests)
            effective_policy = resolve_effective_task_policy(
                run_id=run_id,
                task=task,
                request=request,
                tool_registry=self.tool_registry,
                model_profile=profile,
                agent_profile=agent_profile,
                skill_ids=[s.manifest.id for s in skills],
                pack_id=pack_id,
                pack_version=pack_version,
                connector_tool_names=connector_tool_names,
                grantable_connector_tools=grantable,
                web_search_tool=TOOL_WEB_SEARCH,
                stack_profile_digest=stack_digest,
                stack_profile_artifact_sha256=stack_sha,
                stack_profile_schema_version=stack_version,
                reference_pack_ids=composition_gate.reference_pack_ids,
                profile_ids=[
                    agent_profile,
                    *composition_gate.policy_profile_ids,
                ],
                route_class=route_class,
                fallback_model_profile=fallback_model_profile,
                fallback_eligible=fallback_eligible,
                validator_ids=resolve_validation_command_ids(request)
                or list(self.config.policies.registered_commands),
                prompt_tool_names=prompt_names,
                prompt_reduction_reason=reduction_reason,
                denied_tool_names=workflow_pack.execution_policy.denied_tool_names,
                pack_allowed_tool_classes=(workflow_pack.execution_policy.allowed_tool_classes),
                executor_mode=(
                    workflow_pack.execution_policy.executor_mode_for(task.capability)
                    if is_registered_workflow(request.workflow_type)
                    else require_descriptor(task.capability).executor_mode
                ),
            )
            validate_write_payload(
                EFFECTIVE_TASK_POLICY_SCHEMA,
                effective_policy.model_dump(mode="json"),
            )
            policy_ref = artifacts.put_json(
                effective_policy.model_dump(mode="json"),
                logical_name=f"effective-policy-{task.id}.json",
                created_by_task_id=task.id,
                schema_id=EFFECTIVE_TASK_POLICY_SCHEMA,
                schema_version="1",
            )
            self.db.record_artifact(policy_ref.model_dump(mode="json"))
            self._record_artifact_instance(
                ArtifactInstance.create(
                    run_id=run_id,
                    sha256=policy_ref.sha256,
                    content_class="durable_output",
                    capture_level="full",
                    role="effective_task_policy",
                    producer_task_id=task.id,
                    media_type=policy_ref.media_type,
                    schema_id=EFFECTIVE_TASK_POLICY_SCHEMA,
                    schema_version="1",
                    size_bytes=policy_ref.size_bytes,
                    display_name=policy_ref.logical_name,
                )
            )
            self.db.upsert_task(
                run_id=run_id,
                task_id=task.id,
                capability=task.capability,
                status="running",
                spec=task.model_dump(mode="json"),
                effective_policy=effective_policy.model_dump(mode="json"),
                active_operation=task.capability,
            )

        prompt_set = set(effective_policy.prompt_tool_names)
        tool_defs = [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self.tool_registry.list()
            if t.name in prompt_set
        ]
        registered_ids = resolve_validation_command_ids(request) or list(
            self.config.policies.registered_commands
        )
        if registered_ids:
            for entry in tool_defs:
                if entry.get("name") == "run_validation_command":
                    entry["description"] = (
                        f"{entry.get('description', '')} Registered ids: "
                        f"{', '.join(registered_ids)}."
                    )
        excerpt_root = original_repo
        repository_excerpts: list[dict[str, str]] = []
        context_omissions: list[str] = []
        if skills_disabled:
            context_omissions.append("skills_disabled")
        context_mode = str(request.metadata.get("context_mode") or "targeted").strip().lower()
        soft_limit = profile_cfg.context_soft_limit if profile_cfg is not None else None
        packing_limits = resolve_context_limits(
            self.config.policies.context,
            task_max_input_tokens=task.budget.max_input_tokens,
            model_context_soft_limit=soft_limit,
        )
        if excerpt_root is not None:
            if context_mode in {"file_list_only", "file-list-only", "paths_only"}:
                repository_excerpts, context_omissions = list_repository_paths(
                    excerpt_root,
                    max_files=packing_limits.max_file_list_paths,
                )
            else:
                repository_excerpts, context_omissions = select_repository_excerpts(
                    excerpt_root,
                    objective=f"{request.request_text}\n{task.objective}",
                    max_files=packing_limits.max_excerpt_files,
                    max_chars=packing_limits.max_excerpt_chars,
                )
        runtime_directives: list[str] = []
        if registered_ids and task.capability in {"implementation", "repair"}:
            runtime_directives.append(
                "Validation: call run_validation_command only with a registered "
                f"command_id from [{', '.join(registered_ids)}]. Never use "
                "validator labels (behavioral:...) or raw executables (pytest)."
            )
        if "jitter" in f"{request.request_text}\n{task.objective}".lower() and task.capability in {
            "implementation",
            "repair",
        }:
            runtime_directives.append(
                "Retry/jitter: compute the sleep duration with jitter applied "
                "before calling sleep (e.g. sleep(min(max_delay, delay * "
                "(1 + random())))). Do not sleep the base delay and only mutate "
                "delay afterward — tests assert observed sleep values vary."
            )
        ctx = assemble_context(
            task=task,
            model_profile=profile,
            agent_profile=agent_profile,
            skills=skills,
            tool_definitions=tool_defs,
            repository_excerpts=repository_excerpts,
            dependency_outputs=dependency_outputs,
            context_omissions=context_omissions,
            runtime_directives=runtime_directives or None,
            package_id=f"pkg-{task.id}",
            packing=packing_limits,
            profile_digests=profile_digests,
        )
        task_context = build_task_context(
            task_id=task.id,
            skills=skills,
            tool_names=list(effective_policy.allowed_tool_names),
            prompt_tool_names=list(effective_policy.prompt_tool_names),
            expected_output_schema=task.expected_output_schema,
            profile_digests=profile_digests,
            effective_policy=effective_policy.model_dump(mode="json"),
        )
        persist_task_context(task_context, run_dir / "prompts")
        (run_dir / "prompts" / f"{task.id}.manifest.json").write_text(
            ctx.manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        if recorder is not None:
            recorder.emit(
                run_id=run_id,
                event_type="prompt.package_created",
                task_id=task.id,
                summary="Prompt package assembled",
                payload={
                    "package_hash": ctx.package_hash,
                    "manifest": ctx.manifest.model_dump(mode="json"),
                    "effective_policy": effective_policy.model_dump(mode="json"),
                },
                content=ctx.messages,
                content_logical_name=f"prompt-package-{task.id}",
            )

        wt_path = run_dir / "scratch" / task.id
        wt_path.mkdir(parents=True, exist_ok=True)
        writable = task.capability in {
            "implementation",
            "repair",
            "test_design",
            "composition",
            "interface_analysis",
        }
        inherited_artifacts: list[str] = []
        lineage_conflicts: list[dict[str, str]] = []
        pre_patch_fingerprint: str | None = None
        if worktrees is not None and original_repo is not None and base_commit:
            if task.capability in {
                "implementation",
                "repair",
                "repository_analysis",
                "independent_review",
                "composition",
                "test_execution",
                "domain_research",
                "interface_analysis",
            }:
                (
                    wt_path,
                    inherited_artifacts,
                    lineage_conflicts,
                    pre_patch_fingerprint,
                ) = self.worktree_lineage.prepare_task_worktree(
                    worktrees=worktrees,
                    artifacts=artifacts,
                    run_dir=run_dir,
                    task_id=task.id,
                    capability=task.capability,
                    dependencies=task.dependencies,
                    dependency_outputs=dependency_outputs or [],
                    base_commit=base_commit,
                    writable=writable,
                )

        def _tool_observer(phase: str, payload: dict) -> None:
            if recorder is None:
                return
            severity = EventSeverity.ERROR if phase == "failed" else EventSeverity.INFO
            recorder.emit(
                run_id=run_id,
                event_type=f"tool.call.{phase}",
                task_id=task.id,
                tool_call_id=payload.get("tool_call_id"),
                summary=str(payload.get("tool_name") or phase),
                payload=payload,
                severity=severity,
            )

        def _connector_audit(event_type: str, payload: dict) -> None:
            if recorder is None:
                return
            severity = (
                EventSeverity.INFO if event_type == CONNECTOR_EVENT_INVOKED else EventSeverity.ERROR
            )
            recorder.emit(
                run_id=run_id,
                event_type=event_type,
                task_id=task.id,
                tool_call_id=payload.get("tool_call_id"),
                summary=f"{payload.get('connector_id') or '?'}:{payload.get('tool_name') or '?'}",
                payload=payload,
                severity=severity,
            )

        broker = ToolBroker(
            registry=self.tool_registry,
            artifact_store=artifacts,
            worktree_root=(
                wt_path
                if original_repo or effective_policy.executor_mode == "interface_agent_loop"
                else None
            ),
            original_repo=original_repo,
            registered_commands=self.config.policies.registered_commands,
            base_commit=base_commit or None,
            observer=_tool_observer if recorder is not None else None,
            ledger=ledger,
            connectors=self.connector_broker,
            connector_audit=_connector_audit,
            source_ledger=SourceLedger.for_run(run_dir),
            source_policy=resolve_request_source_policy(
                request, profiles_root=self.config.root / "profiles"
            ),
            # SD0.C temporary: remove when deployment executor owns ApprovalService
            # consumption (issue: remove-coordinator-approval-verify-2026-08).
            connector_approval_verified=self._deployment_approval_verified(
                request, consumer_run_id=run_id, capability=task.capability
            ),
            run_id=run_id,
            capture_level=recorder.capture_level if recorder is not None else None,
            on_artifact_instance=self._record_artifact_instance,
        )
        granted = set(effective_policy.allowed_tool_names)

        # Fail closed before granting if a matched skill's declared tool policy
        # is inconsistent with the task's actual grant (P1.E).
        enforce_skill_grants(
            skills=skills,
            granted_tool_names=granted,
            connector_tools=self.connector_registry.tool_names_by_class(),
        )

        broker.set_grant(
            CapabilityGrant(
                grant_id=f"grant-{task.id}",
                run_id=run_id,
                task_id=task.id,
                agent_profile=agent_profile,
                tool_names=granted,
                allowed_path_patterns=task.allowed_path_patterns,
                readable_path_patterns=task.effective_read_patterns(),
                writable_path_patterns=task.effective_write_patterns(),
                # Reserve headroom for post-loop system git_diff / status calls.
                max_calls=int(
                    effective_policy.call_limits.get(
                        "max_calls",
                        max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10),
                    )
                ),
            )
        )

        if lineage_conflicts and task.capability == "composition":
            conflict_result = TaskResult(
                task_id=task.id,
                status="failed",
                summary="composition_conflict",
                validator_results=[
                    ValidatorResult(
                        validator_id="composition_conflict",
                        status="fail",
                        message="Conflicting writable-task patches detected",
                        details={"conflicts": lineage_conflicts},
                    )
                ],
                model_profile=profile,
            )
            # Early-exit path (P1.F): still persist the terminal status so the
            # task row doesn't stay stuck at "running" for resume/observability.
            self.db.upsert_task(
                run_id=run_id,
                task_id=task.id,
                capability=task.capability,
                status="failed",
                spec=task.model_dump(mode="json"),
                result=conflict_result.model_dump(mode="json"),
                ended_at=datetime.now(UTC).isoformat(),
                active_operation=None,
            )
            return conflict_result

        # SD1: dispatch declared work through the executor registry.
        # SD1 temporary: composition still receives coordinator compose callbacks
        # (issue: remove-coordinator-compose-callbacks-2026-08).
        descriptor = require_descriptor(task.capability)
        execution_request = TaskExecutionRequest(
            run_id=run_id,
            run_dir=run_dir,
            request=request,
            task=task,
            effective_policy=effective_policy,
            descriptor=descriptor,
            agent_profile=agent_profile,
            model_profile=profile,
            broker=broker,
            artifacts=artifacts,
            gateway=gateway,
            raw_gateway=self._raw_gateway,
            tool_registry=self.tool_registry,
            allow_deterministic_workers=self.allow_deterministic_workers,
            ctx_messages=ctx.messages,
            package_hash=ctx.package_hash,
            granted_tool_names=granted,
            registered_command_ids=list(registered_ids),
            dependency_outputs=dependency_outputs or [],
            repository_excerpts=repository_excerpts,
            base_commit=base_commit,
            land_map=land_map,
            composer_role=composer_role,
            validation_evidence_refs=validation_evidence_refs,
            validator_results=validator_results,
            composition=self.composition,
            services={
                "deterministic_impl_files": deterministic_impl_files,
                "changed_files_from_patch": changed_files_from_patch,
            },
        )
        result = execute_task(execution_request)

        # Legacy live probe path removed: architecture/requirements now persist drafts above.
        context_evidence = [
            ResourceRef(
                id=f"context:{task.id}:{excerpt['path']}",
                resource_type="file",
                origin="run_coordinator",
                scope=excerpt["path"],
                trust_level="mixed",
                content_hash=hashlib.sha256(excerpt["content"].encode()).hexdigest(),
            )
            for excerpt in repository_excerpts
        ]
        if not result.evidence_refs:
            result.evidence_refs = context_evidence
        if not result.provider:
            result.provider = getattr(gateway, "default_model", type(gateway).__name__)
        if not result.prompt_package_hash:
            result.prompt_package_hash = ctx.package_hash
        artifact_refs = list(result.artifact_refs)
        self.db.upsert_task(
            run_id=run_id,
            task_id=task.id,
            capability=task.capability,
            status=result.status,
            spec=task.model_dump(mode="json"),
            result=result.model_dump(mode="json"),
            ended_at=datetime.now(UTC).isoformat(),
            active_operation=None,
        )
        for art in artifact_refs:
            self.db.record_artifact(art.model_dump(mode="json"))
            self._record_artifact_instance(
                ArtifactInstance.create(
                    run_id=run_id,
                    sha256=art.sha256,
                    content_class="durable_output",
                    capture_level=recorder.capture_level if recorder is not None else "full",
                    role=art.logical_name,
                    producer_task_id=task.id,
                    media_type=art.media_type,
                    schema_id=art.schema_id,
                    schema_version=art.schema_version,
                    size_bytes=art.size_bytes,
                    display_name=art.logical_name,
                )
            )
            if recorder is not None:
                recorder.emit(
                    run_id=run_id,
                    event_type="artifact.created",
                    task_id=task.id,
                    summary=art.logical_name,
                    payload=art.model_dump(mode="json"),
                )
        for tc in broker.history:
            self.db.record_tool_call(run_id=run_id, record=tc.model_dump(mode="json"))
        return result

    def _count_connector_invocations(self, run_id: str, *, connector_id: str) -> int:
        """How many successful connector.invoked events this run recorded for a provider."""
        count = 0
        for event in self.db.list_events(
            run_id=run_id, after_seq=0, limit=10_000, types=[CONNECTOR_EVENT_INVOKED]
        ):
            payload = event.get("payload_json") or event.get("payload") or "{}"
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    payload = {}
            if str(payload.get("connector_id") or "") == connector_id:
                count += 1
        return count

    def approve(self, run_id: str, *, apply: bool = False) -> dict[str, Any]:
        run_dir = self.pf_root / "runs" / run_id
        approval_path = run_dir / "output" / "approval.json"
        if not approval_path.exists():
            raise ApprovalBlockedError(f"No pending approval for {run_id}")
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        approval["status"] = "approved"
        approval["decided_at"] = datetime.now(UTC).isoformat()
        approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
        row = self.db.get_run(run_id)
        if apply:
            return self.apply_patch(run_id)
        if row:
            req = json.loads(row["request_json"])
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=row["workflow_type"],
                status="completed",
                request=req,
                base_commit=row.get("base_commit"),
                usage=json.loads(row.get("usage_json") or "{}"),
            )
        self._emit_approval_decided(run_id, "approved")
        return approval

    def reject(self, run_id: str) -> dict[str, Any]:
        run_dir = self.pf_root / "runs" / run_id
        approval_path = run_dir / "output" / "approval.json"
        if not approval_path.exists():
            raise ApprovalBlockedError(f"No pending approval for {run_id}")
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        approval["status"] = "rejected"
        approval["decided_at"] = datetime.now(UTC).isoformat()
        approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
        row = self.db.get_run(run_id)
        if row:
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=row["workflow_type"],
                status="blocked",
                request=json.loads(row["request_json"]),
                base_commit=row.get("base_commit"),
            )
        self._emit_approval_decided(run_id, "rejected")
        return approval

    def cancel(self, run_id: str) -> dict[str, Any]:
        """Request cooperative cancel; flip status immediately when no worker loop."""
        row = self.db.get_run(run_id)
        if not row:
            raise ConfigurationError(f"Unknown run: {run_id}")
        status = str(row["status"])
        terminal = {
            "completed",
            "failed",
            "blocked",
            "budget_exhausted",
            "plan_rejected",
            "cancelled",
        }
        if status in terminal:
            if status == "cancelled":
                return {
                    "run_id": run_id,
                    "status": "cancelled",
                    "cancel_requested": True,
                    "immediate": True,
                }
            raise ConfigurationError(
                f"Run {run_id} is already terminal ({status}); cannot cancel",
                details={"status": status},
            )

        self.db.set_cancel_requested(run_id, requested=True)
        recorder = TelemetryRecorder(self.db)
        recorder.emit(
            run_id=run_id,
            event_type="run.cancel_requested",
            severity=EventSeverity.WARNING,
            summary="Cancel requested",
            payload={"previous_status": status},
        )

        # No active wave loop for queued / awaiting_approval — finalize now.
        immediate = status in {"queued", "awaiting_approval"}
        if immediate:
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=row["workflow_type"],
                status="cancelled",
                request=json.loads(row["request_json"]),
                base_commit=row.get("base_commit"),
                usage=json.loads(row.get("usage_json") or "{}"),
                active_operation=None,
            )
            if status == "awaiting_approval":
                approval_path = self.pf_root / "runs" / run_id / "output" / "approval.json"
                if approval_path.exists():
                    try:
                        approval = json.loads(approval_path.read_text(encoding="utf-8"))
                    except json.JSONDecodeError:
                        approval = {}
                    approval["status"] = "cancelled"
                    approval["decided_at"] = datetime.now(UTC).isoformat()
                    approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")
            recorder.emit(
                run_id=run_id,
                event_type="run.cancelled",
                severity=EventSeverity.WARNING,
                summary="Run cancelled",
                payload={"status": "cancelled", "immediate": True},
            )
            status = "cancelled"

        return {
            "run_id": run_id,
            "status": status,
            "cancel_requested": True,
            "immediate": immediate,
        }

    def revise(self, run_id: str, *, note: str) -> RunManifest:
        """Bounded follow-up after awaiting_approval; does not widen grants."""
        note = (note or "").strip()
        if not note:
            raise ConfigurationError("Revision note is required")
        row = self.db.get_run(run_id)
        if not row:
            raise ConfigurationError(f"Unknown run: {run_id}")
        if row["status"] != "awaiting_approval":
            raise ApprovalBlockedError(
                f"Revise requires awaiting_approval; run is {row['status']!r}",
                details={"status": row["status"]},
            )

        run_dir = self.pf_root / "runs" / run_id
        approval_path = run_dir / "output" / "approval.json"
        if not approval_path.exists():
            raise ApprovalBlockedError(f"No pending approval for {run_id}")
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        prior_actions = list(approval.get("actions") or [])
        approval["status"] = "revision_requested"
        approval["revision_note"] = note
        approval["revised_at"] = datetime.now(UTC).isoformat()
        approval_path.write_text(json.dumps(approval, indent=2), encoding="utf-8")

        request = RunRequest.model_validate(json.loads(row["request_json"]))
        # Boundedness: same workflow, budget, validation commands, and policy.
        # Only attach the operator note — never widen tool/skill grants here.
        revision_count = int(request.metadata.get("revision_count") or "0") + 1
        metadata = dict(request.metadata)
        metadata["revision_count"] = str(revision_count)
        metadata["revision_note"] = note
        revised_text = request.request_text.rstrip()
        if note not in revised_text:
            revised_text = f"{revised_text}\n\n## Operator revision\n{note}\n"
        revised = request.model_copy(update={"request_text": revised_text, "metadata": metadata})

        revisions_path = run_dir / "output" / "revisions.jsonl"
        with revisions_path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "at": datetime.now(UTC).isoformat(),
                        "note": note,
                        "revision_count": revision_count,
                        "workflow_type": revised.workflow_type,
                        "budget": revised.budget.model_dump(mode="json"),
                        "prior_approval_actions": prior_actions,
                    },
                    default=str,
                )
                + "\n"
            )

        recorder = TelemetryRecorder(self.db)
        recorder.emit(
            run_id=run_id,
            event_type="run.revision_requested",
            summary="Operator requested revision",
            payload={
                "note": note,
                "revision_count": revision_count,
                "workflow_type": revised.workflow_type,
                "grants_unchanged": True,
            },
        )

        self.db.set_cancel_requested(run_id, requested=False)
        (run_dir / "input" / "request.md").write_text(revised.request_text, encoding="utf-8")
        (run_dir / "input" / "request.json").write_text(
            revised.model_dump_json(indent=2), encoding="utf-8"
        )
        persist_pack_input(revised.pack_input, run_dir / "input")
        # Fresh worktrees for the follow-up (same run_id); do not reuse stale
        # implementation trees from the prior awaiting_approval attempt.
        worktrees_root = run_dir / "worktrees"
        if revised.repository_path is not None and worktrees_root.exists():
            repo = revised.repository_path.resolve()
            for child in list(worktrees_root.iterdir()):
                if not child.is_dir():
                    continue
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(child)],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if child.exists():
                    shutil.rmtree(child, ignore_errors=True)
            subprocess.run(
                ["git", "worktree", "prune"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
        self.db.upsert_run(
            run_id=run_id,
            workflow_type=revised.workflow_type,
            status="planning",
            request=revised.model_dump(mode="json"),
            base_commit=row.get("base_commit"),
            usage=json.loads(row.get("usage_json") or "{}"),
            active_operation="revising",
        )
        return self.run(revised, run_id=run_id)

    def _raise_if_cancelled(self, run_id: str) -> None:
        if self.db.is_cancel_requested(run_id):
            raise RunCancelledError(f"Run {run_id} cancelled by operator")

    def _emit_approval_decided(self, run_id: str, decision: str) -> None:
        recorder = TelemetryRecorder(self.db)
        recorder.emit(
            run_id=run_id,
            event_type="approval.decided",
            summary=f"Approval {decision}",
            payload={"decision": decision},
        )

    def apply_patch(self, run_id: str) -> dict[str, Any]:
        run_dir = self.pf_root / "runs" / run_id
        approval_path = run_dir / "output" / "approval.json"
        if approval_path.exists():
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            if approval.get("status") not in {"approved", "awaiting_approval"}:
                raise ApprovalBlockedError("Patch not approved")
            if approval.get("status") == "awaiting_approval":
                raise ApprovalBlockedError("Approve before apply")
        else:
            raise ApprovalBlockedError("No approval record")
        row = self.db.get_run(run_id)
        if not row:
            raise RuntimeFailureError(f"Unknown run {run_id}")
        req = json.loads(row["request_json"])
        repo = Path(req.get("repository_path") or "")
        patch = (run_dir / "output" / "proposed.patch").read_text(encoding="utf-8")
        apply_patch(repo, patch)
        self.db.upsert_run(
            run_id=run_id,
            workflow_type=row["workflow_type"],
            status="completed",
            request=req,
            base_commit=row.get("base_commit"),
        )
        return {"run_id": run_id, "applied": True, "repository": str(repo)}
