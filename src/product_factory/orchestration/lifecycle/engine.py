"""RunLifecycleEngine — submit/execute/resume/cancel/revise/approve orchestration (SD2)."""

from __future__ import annotations

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
from product_factory.connectors.registry import ConnectorRegistry
from product_factory.connectors.tavily import CONNECTOR_ID as TAVILY_CONNECTOR_ID
from product_factory.domain.artifacts import ResourceRef
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
from product_factory.domain.plans import CompiledPlan
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.domain.tasks import (
    TaskResult,
    TaskSpec,
    is_terminal_task_status,
    requires_repair_or_terminal_resolution,
    satisfies_dependency,
)
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.observability.contracts import EventSeverity
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.composition.input import composition_input_from_compose_context
from product_factory.orchestration.composition.service import CompositionService
from product_factory.orchestration.effective_policy import (
    EFFECTIVE_TASK_POLICY_SCHEMA,
)
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.orchestration.finalization.run_finalizer import RunFinalizer
from product_factory.orchestration.lifecycle.admission import RunAdmissionService
from product_factory.orchestration.lifecycle.session import RunExecutionSessionFactory
from product_factory.orchestration.planning import RunPlanningService
from product_factory.orchestration.repair import (
    create_repair_tasks,
    patch_fingerprint,
    should_terminate_no_progress,
    update_no_progress,
)
from product_factory.orchestration.task_contracts import (
    TaskPreparationRequest,
    TaskRuntimeRequest,
)
from product_factory.orchestration.task_preparation import (
    BlockedPreparation,
    TaskPreparationService,
)
from product_factory.orchestration.task_runtime import TaskRuntimeService
from product_factory.orchestration.validation_repair.service import (
    ValidationRepairService,
    changed_files_from_patch,
    resolve_validation_command_ids,
)
from product_factory.orchestration.wave_execution import WaveExecutionService
from product_factory.orchestration.worktree_lineage import WorktreeLineageService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.persistence.database import Database
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
)
from product_factory.repositories.patches import (
    apply_patch,
    create_patch,
)
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.registry import ToolRegistry
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
from product_factory.workflows.inputs import persist_pack_input
from product_factory.workflows.registry import (
    is_registered_workflow,
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


class RunLifecycleEngine:
    def __init__(
        self,
        *,
        config: AppConfig,
        pf_root: Path,
        db: Database,
        skills: SkillRegistry,
        tool_registry: ToolRegistry,
        connector_registry: ConnectorRegistry,
        connector_broker: ConnectorBroker,
        raw_gateway: ModelGateway,
        composition: CompositionService,
        validation_repair: ValidationRepairService,
        worktree_lineage: WorktreeLineageService,
        finalizer: RunFinalizer,
        task_preparation: TaskPreparationService,
        task_runtime: TaskRuntimeService,
        wave_execution: WaveExecutionService,
        session_factory: RunExecutionSessionFactory,
        admission: RunAdmissionService,
        planning: RunPlanningService,
        allow_deterministic_workers: bool,
        use_deterministic_planner: bool,
    ) -> None:
        self.config = config
        self.allow_deterministic_workers = allow_deterministic_workers
        self.use_deterministic_planner = use_deterministic_planner
        self.pf_root = pf_root
        self.db = db
        self.skills = skills
        self.tool_registry = tool_registry
        self.connector_registry = connector_registry
        self.connector_broker = connector_broker
        self._raw_gateway = raw_gateway
        self.composition = composition
        self.validation_repair = validation_repair
        self.worktree_lineage = worktree_lineage
        self.finalizer = finalizer
        self.task_preparation = task_preparation
        self.task_runtime = task_runtime
        self.wave_execution = wave_execution
        self.session_factory = session_factory
        self.admission = admission
        self.planning = planning

    def run(self, request: RunRequest, *, run_id: str | None = None) -> RunManifest:
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        run_dir = self.pf_root / "runs" / run_id
        execution_context = self.session_factory.open(
            run_id=run_id,
            request=request,
            run_dir=run_dir,
        )
        events = execution_context.events
        recorder = execution_context.recorder
        usage = UsageMetrics()
        base_commit: str | None = None
        worktrees: WorktreeManager | None = None
        original_repo: Path | None = None
        workflow_pack: WorkflowPack | None = None
        land_map = ArtifactLandMap()

        try:
            snapshot, execution_context = self.admission.admit(
                run_id=run_id,
                request=request,
                session=execution_context,
            )
            workflow_pack = snapshot.workflow_pack
            land_map = snapshot.land_map
            base_commit = snapshot.repository.base_commit
            original_repo = snapshot.repository.original_path
            worktrees = snapshot.repository.worktrees
            planning = self.planning.ensure_plan(snapshot=snapshot, session=execution_context)
            plan = planning.compiled_plan

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
        # Persisted grants are authoritative.  An unfinished legacy or
        # tampered run must be restarted, never resumed under broadened
        # authority. Pending tasks without a persisted policy are prepared
        # fresh; this check applies to grants that already exist on disk.
        from product_factory.orchestration.effective_policy import EffectiveTaskPolicy

        incompatible_task_ids: list[str] = []
        for row in self.db.list_tasks_in_creation_order(run_id):
            payload = row.get("effective_policy_json")
            if not payload:
                continue
            try:
                policy = EffectiveTaskPolicy.model_validate_json(payload)
                policy.ensure_valid_digest()
            except (TypeError, ValueError):
                incompatible_task_ids.append(str(row.get("task_id") or "unknown"))
                continue
            if row.get("effective_policy_schema") != EFFECTIVE_TASK_POLICY_SCHEMA or (
                row.get("effective_policy_digest") != policy.policy_digest
            ):
                incompatible_task_ids.append(policy.task_id)
        if incompatible_task_ids:
            reason = {
                "code": "policy_incompatible",
                "next_action": "restart_required",
                "task_ids": sorted(incompatible_task_ids),
            }
            self.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="blocked",
                request=request.model_dump(mode="json"),
                base_commit=run_row.get("base_commit"),
                active_operation="restart_required",
                blocked_reason=reason,
            )
            TelemetryRecorder(self.db).emit(
                run_id=run_id,
                event_type="run.policy_incompatible",
                severity=EventSeverity.ERROR,
                summary="Persisted effective policy is incompatible; restart required",
                payload=reason,
            )
            raise ConfigurationError("policy_incompatible: restart_required", details=reason)
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
        execution_context = self.session_factory.open(
            run_id=run_id,
            request=request,
            run_dir=run_dir,
            budget_snapshot=budget_snapshot,
        )
        artifacts = execution_context.artifacts
        recorder = execution_context.recorder
        ledger = execution_context.ledger
        snapshot, execution_context = self.admission.resume(
            run_id=run_id,
            request=request,
            status=str(run_row["status"]),
            base_commit=base_commit,
            session=execution_context,
        )
        workflow_pack = snapshot.workflow_pack
        land_map = snapshot.land_map
        existing_plan = self.planning.load_existing(snapshot=snapshot)
        merged_tasks = dict(existing_plan.compiled_plan.tasks)
        merged_order = list(existing_plan.compiled_plan.task_order)

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
            if is_terminal_task_status(status) and row.get("result_json"):
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
        live_plan = existing_plan.compiled_plan.model_copy(
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
                    tid for tid, st in task_status.items() if satisfies_dependency(st)
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

            def _mark_task_started(task: TaskSpec) -> None:
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

            def _before_dispatch(spent: Decimal = spent) -> None:
                if spent >= request.budget.max_cost_usd:
                    raise BudgetExhaustedError("Run budget exhausted before wave")
                ledger.check_wall_clock()

            def _run_one(
                task: TaskSpec,
                dependency_outputs: list[dict[str, Any]],
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
                    dependency_outputs=dependency_outputs,
                    land_map=land_map,
                    composer_role=composer_roles.get(task.id),
                    validation_evidence_refs=validation_evidence_refs,
                    validator_results=collected_validator_results,
                )

            # Execute wave: read-only tasks and predicted-disjoint writers run
            # concurrently (bounded by max_parallel_tasks via WaveExecutionService);
            # conflicting writers are serialized. Result processing below stays
            # single-threaded and iterates `ready` (plan order) regardless of
            # completion order, giving a deterministic merge order (P1.F).
            cycle = self.wave_execution.run_wave_cycle(
                plan=live_plan,
                task_status=task_status,
                max_parallel=request.budget.max_parallel_tasks,
                prior_results=list(results),
                artifacts=artifacts,
                mark_task_started=_mark_task_started,
                executor_fn=_run_one,
                before_dispatch=_before_dispatch,
            )
            ready = cycle.ready
            wave_results = cycle.wave_results
            if not ready:
                if all(is_terminal_task_status(task_status[t]) for t in live_plan.tasks):
                    break
                # deadlock — diagnose terminal unsuccessful deps instead of
                # reporting an opaque unsatisfiable-scheduler failure when
                # dependents are blocked on partial/failed/blocked work.
                pending = [t for t, s in task_status.items() if s == "pending"]
                if pending:
                    failed = [
                        f"{result.task_id}: {result.summary}"
                        for result in results
                        if requires_repair_or_terminal_resolution(result.status)
                    ]
                    if failed:
                        raise RuntimeFailureError("Dependency failed; " + "; ".join(failed))
                    raise RuntimeFailureError(f"Unsatisfiable dependencies for tasks: {pending}")
                break

            execution_context.cancel_check()

            for task, result in zip(ready, wave_results, strict=True):
                usage = usage.merge(result.usage)
                spent = usage.estimated_cost_usd
                task_status[task.id] = "success" if result.status == "success" else result.status
                results.append(result)
                findings.extend(result.findings)
                if result.validator_results:
                    collected_validator_results.extend(
                        [v.model_dump(mode="json") for v in result.validator_results]
                    )
                # task.completed / task.failed already committed with the task
                # row via WaveExecutionService.record_task_completion (SR3).
                # Budget telemetry stays on the recorder path after that commit.
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
                            or self.validation_repair.is_repair_eligible(
                                capability=live_plan.tasks[result.task_id].capability,
                                workflow_pack=workflow_pack,
                            )
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
                                compose_ctx = ComposeContext(
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
                                )
                                compose_ctx.composition_input = (
                                    composition_input_from_compose_context(compose_ctx)
                                )
                                document = pack_handler.compose(
                                    entry.role,
                                    compose_ctx,
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

    def _record_artifact_instance(self, instance: ArtifactInstance) -> None:
        self.db.record_artifact_instance(instance.model_dump(mode="json"))

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
        """Prepare and dispatch one task; wave ownership persists its outcome."""

        prepared = self.task_preparation.prepare(
            TaskPreparationRequest(
                execution_session=execution_context,
                run_request=request,
                task=task,
                worktrees=worktrees,
                original_repository=original_repo,
                base_commit=base_commit,
                dependency_outputs=tuple(dependency_outputs or ()),
                land_map=land_map or ArtifactLandMap(),
                composition_role=composer_role,
                validation_evidence_refs=tuple(validation_evidence_refs or ()),
                validator_results=tuple(validator_results or ()),
            )
        )
        if isinstance(prepared, BlockedPreparation):
            self.wave_execution.record_task_completion(
                db=self.db,
                run_id=execution_context.run_id,
                task=task,
                result=prepared.result,
            )
            return prepared.result

        runtime_outcome = self.task_runtime.execute(
            TaskRuntimeRequest(
                prepared_task=prepared,
                execution_session=execution_context,
            )
        )
        result = runtime_outcome.result
        if not result.evidence_refs:
            result.evidence_refs = [
                ResourceRef(
                    id=f"context:{task.id}:{excerpt['path']}",
                    resource_type="file",
                    origin="task",
                    scope=excerpt["path"],
                    trust_level="mixed",
                    content_hash=hashlib.sha256(excerpt["content"].encode()).hexdigest(),
                )
                for excerpt in prepared.repository_excerpts
            ]
        if not result.provider:
            gateway = execution_context.gateway
            result.provider = getattr(gateway, "default_model", type(gateway).__name__)
        if not result.prompt_package_hash:
            result.prompt_package_hash = prepared.prompt_package.package_hash

        self.wave_execution.record_task_completion(
            db=self.db,
            run_id=execution_context.run_id,
            task=task,
            result=result,
        )
        for artifact in result.artifact_refs:
            self.db.record_artifact(artifact.model_dump(mode="json"))
            self._record_artifact_instance(
                ArtifactInstance.create(
                    run_id=execution_context.run_id,
                    sha256=artifact.sha256,
                    content_class="durable_output",
                    capture_level=execution_context.recorder.capture_level,
                    role=artifact.logical_name,
                    producer_task_id=task.id,
                    media_type=artifact.media_type,
                    schema_id=artifact.schema_id,
                    schema_version=artifact.schema_version,
                    size_bytes=artifact.size_bytes,
                    display_name=artifact.logical_name,
                )
            )
            execution_context.recorder.emit(
                run_id=execution_context.run_id,
                event_type="artifact.created",
                task_id=task.id,
                summary=artifact.logical_name,
                payload=artifact.model_dump(mode="json"),
            )
        for tool_call in runtime_outcome.tool_call_records:
            self.db.record_tool_call(
                run_id=execution_context.run_id,
                record=tool_call.model_dump(mode="json"),
            )
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
