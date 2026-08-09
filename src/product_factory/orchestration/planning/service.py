"""Plan drafting, registered transforms, compilation, repair, and persistence."""

from __future__ import annotations

import json
import shutil
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.domain.budgets import TaskBudgetDefaults, clamp_task_budget
from product_factory.domain.errors import PlanRejectedError
from product_factory.domain.plans import PlannerOutput
from product_factory.domain.runs import RunRequest
from product_factory.observability.contracts import EventSeverity
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.orchestration.lifecycle.models import PlanningOutcome, RunSnapshot
from product_factory.orchestration.validation_repair.service import resolve_validation_command_ids
from product_factory.persistence.database import Database
from product_factory.planning.compiler import compile_plan
from product_factory.planning.planner import plan_with_gateway
from product_factory.policy.domain_packs import resolve_request_domain_packs
from product_factory.policy.policy_profiles import resolve_request_policy_profiles
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.repository.stack_profile import discover_stack_profile
from product_factory.skills.profiles import ProfileRegistry
from product_factory.skills.registry import SkillRegistry
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.plan_transforms import apply_plan_transforms


class RunPlanningService:
    """Own the complete durable planning phase."""

    def __init__(
        self,
        *,
        config: AppConfig,
        database: Database,
        skills: SkillRegistry,
        use_deterministic_planner: bool,
    ) -> None:
        self._config = config
        self._database = database
        self._skills = skills
        self._use_deterministic_planner = use_deterministic_planner

    def ensure_plan(
        self, *, snapshot: RunSnapshot, session: RunExecutionContext
    ) -> PlanningOutcome:
        request = snapshot.request
        pack = snapshot.workflow_pack
        self._database.upsert_run(
            run_id=snapshot.run_id,
            workflow_type=request.workflow_type,
            status="planning",
            request=request.model_dump(mode="json"),
            base_commit=snapshot.repository.base_commit,
            active_operation="planning",
            workflow_pack_digest=pack.content_hash() if pack else None,
        )
        session.recorder.emit(
            run_id=snapshot.run_id,
            event_type="run.status_changed",
            summary="Planning",
            payload={"status": "planning"},
        )
        proposal = self._draft(snapshot=snapshot, session=session)
        artifact = session.artifacts.put_json(
            proposal.model_dump(mode="json"), logical_name="plan.json", created_by_task_id="plan"
        )
        shutil.copy(
            session.artifacts.blobs / artifact.sha256,
            snapshot.run_dir / "output" / "plan.json",
        )
        result = self._compile(proposal, request=request, pack=pack)
        if not result.ok:
            session.recorder.emit(
                run_id=snapshot.run_id,
                event_type="plan.rejected",
                severity=EventSeverity.WARNING,
                summary="Plan rejected by compiler",
                payload={"errors": [error.model_dump() for error in result.errors]},
            )
            if request.budget.max_plan_repairs > 0:
                proposal = self._draft(
                    snapshot=snapshot,
                    session=session,
                    repair_errors=[error.model_dump() for error in result.errors],
                )
                result = self._compile(proposal, request=request, pack=pack)
            if not result.ok:
                report = snapshot.run_dir / "output" / "compiler-report.json"
                report.write_text(
                    json.dumps(
                        {"ok": False, "errors": [error.model_dump() for error in result.errors]},
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                raise PlanRejectedError(
                    "Plan rejected after repair",
                    details={"errors": [error.model_dump() for error in result.errors]},
                )
        plan = result.plan
        assert plan is not None
        report = snapshot.run_dir / "output" / "compiler-report.json"
        report.write_text(
            json.dumps({"ok": True, "notes": plan.compiler_notes}, indent=2), encoding="utf-8"
        )
        session.recorder.emit(
            run_id=snapshot.run_id,
            event_type="plan.compiled",
            summary="Plan compiled",
            payload={
                "task_count": len(plan.tasks),
                "task_order": list(plan.task_order),
                "notes": plan.compiler_notes,
            },
        )
        session.cancel_check()
        return PlanningOutcome(
            snapshot=snapshot,
            compiled_plan=plan,
            compiler_report_path=report,
            plan_artifact_sha256=artifact.sha256,
        )

    def load_existing(self, *, snapshot: RunSnapshot) -> PlanningOutcome:
        """Load and recompile a persisted proposal for fail-closed resume."""

        path = snapshot.run_dir / "output" / "plan.json"
        if not path.exists():
            from product_factory.domain.errors import ConfigurationError

            raise ConfigurationError(
                f"No persisted plan for run {snapshot.run_id}; cannot resume before planning completed"
            )
        proposal = PlannerOutput.model_validate(json.loads(path.read_text(encoding="utf-8")))
        result = self._compile(
            proposal,
            request=snapshot.request,
            pack=snapshot.workflow_pack,
        )
        if not result.ok or result.plan is None:
            raise PlanRejectedError(
                "Persisted plan no longer compiles",
                details={"errors": [error.model_dump() for error in result.errors]},
            )
        return PlanningOutcome(
            snapshot=snapshot,
            compiled_plan=result.plan,
            compiler_report_path=snapshot.run_dir / "output" / "compiler-report.json",
            plan_artifact_sha256="",
            reused_existing=True,
        )

    def _compile(self, proposal: PlannerOutput, *, request: RunRequest, pack):
        return compile_plan(
            proposal,
            max_tasks=request.budget.max_tasks,
            max_parallel_tasks=request.budget.max_parallel_tasks,
            workflow_pack=pack,
            skill_registry=self._skills,
            profile_digests=self.profile_digests(request),
        )

    def _draft(
        self,
        *,
        snapshot: RunSnapshot,
        session: RunExecutionContext,
        repair_errors: list[dict[str, Any]] | None = None,
    ) -> PlannerOutput:
        request = snapshot.request
        planner_mode = str(request.metadata.get("planner_mode") or "").strip().lower()
        force_fixed = planner_mode in {"fixed", "complexity_sensitive", "deterministic"}
        force_live = planner_mode == "live"
        deterministic = force_fixed or (self._use_deterministic_planner and not force_live)
        if deterministic:
            if snapshot.workflow_pack is not None:
                proposal = handler_for(snapshot.workflow_pack.id).plan_template(
                    request.request_text
                )
            else:
                from product_factory.workflows.default_plans import default_code_change_plan

                proposal = default_code_change_plan(request.request_text)
        else:
            proposal = plan_with_gateway(
                session.gateway,
                run_id=snapshot.run_id,
                request_text=request.request_text,
                workflow_type=request.workflow_type,
                repository_summary=snapshot.repository.summary,
                budget=request.budget.model_dump(mode="json"),
                repair_errors=repair_errors,
                allowed_capabilities=(
                    snapshot.workflow_pack.allowed_capabilities
                    if snapshot.workflow_pack is not None
                    else None
                ),
                seed=(
                    int(request.metadata["benchmark_seed"])
                    if request.metadata.get("benchmark_seed") is not None
                    else None
                ),
            )
            if snapshot.workflow_pack is not None:
                allowed = snapshot.workflow_pack.allowed_capabilities
                filtered = [task for task in proposal.tasks if task.capability in allowed]
                if filtered:
                    kept = {task.id for task in filtered}
                    filtered = [
                        task.model_copy(
                            update={
                                "dependencies": [
                                    dependency
                                    for dependency in task.dependencies
                                    if dependency in kept
                                ]
                            }
                        )
                        for task in filtered
                    ]
                    finals = [
                        artifact
                        for artifact in proposal.final_artifacts
                        if artifact.composer_task_id in kept
                    ]
                    proposal = proposal.model_copy(
                        update={
                            "tasks": filtered,
                            "final_artifacts": finals or proposal.final_artifacts,
                        }
                    )
        defaults = getattr(self._config.policies, "budgets", None)
        task_defaults: TaskBudgetDefaults = (
            defaults.task if defaults is not None else TaskBudgetDefaults()
        )
        proposal = proposal.model_copy(
            update={
                "tasks": [
                    task.model_copy(
                        update={"budget": clamp_task_budget(task.budget, defaults=task_defaults)}
                    )
                    for task in proposal.tasks
                ]
            }
        )
        return apply_plan_transforms(proposal, request=request, pack=snapshot.workflow_pack)

    def profile_digests(self, request: RunRequest) -> dict[str, str]:
        digests = ProfileRegistry.load(self._config.root / "profiles").digests()
        if request.repository_path is not None:
            stack_profile = discover_stack_profile(
                request.repository_path,
                registered_command_ids=(
                    resolve_validation_command_ids(request)
                    or self._config.policies.registered_commands
                ),
            )
            digests.update(stack_profile.as_manifest_entry())
        source_policy = resolve_request_source_policy(
            request, profiles_root=self._config.root / "profiles"
        )
        if source_policy is not None:
            digests.update(source_policy.as_manifest_entry())
        for domain_pack in resolve_request_domain_packs(
            request, packs_root=self._config.root / "packs"
        ):
            digests.update(domain_pack.as_manifest_entry())
        for profile in resolve_request_policy_profiles(
            request, profiles_root=self._config.root / "profiles"
        ):
            digests.update(profile.as_manifest_entry())
        return digests
