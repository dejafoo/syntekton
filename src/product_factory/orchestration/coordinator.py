"""Run coordinator — end-to-end orchestration without provider-specific logic."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.context.assembler import (
    assemble_context,
    list_repository_paths,
    select_repository_excerpts,
)
from product_factory.domain.artifacts import ResourceRef
from product_factory.domain.errors import (
    ApprovalBlockedError,
    BudgetExhaustedError,
    PlanRejectedError,
    RuntimeFailureError,
    ValidationFailureError,
)
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.plans import CompiledPlan, FinalArtifactSpec, PlannerOutput
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskResult, TaskSpec
from product_factory.domain.tools import CapabilityGrant
from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolDefinition,
    ModelRequest,
)
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.observability.contracts import EventSeverity
from product_factory.observability.events import EventLog
from product_factory.observability.otel import maybe_create_otel_bridge
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.agent_loop import run_tool_agent
from product_factory.orchestration.repair import (
    create_repair_tasks,
    patch_fingerprint,
    should_terminate_no_progress,
    update_no_progress,
)
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.planning.compiler import compile_plan
from product_factory.planning.planner import plan_with_gateway
from product_factory.repositories.patches import (
    apply_patch,
    apply_patch_check,
    changed_paths_from_patch,
    create_patch,
    detect_writer_conflicts,
)
from product_factory.repositories.snapshot import snapshot_repository
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.scheduling.scheduler import runnable_tasks, select_model
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry
from product_factory.validation.pipeline import (
    has_blocking_failures,
    validate_architecture_document,
    validate_behavioral_commands,
    validate_patch_applies,
    validate_path_scope,
    validate_secrets,
)


def default_code_change_plan(request_text: str) -> PlannerOutput:
    """Risk-aware deterministic plan used by offline tests."""
    proposal = PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Inspect repository structure",
                capability="repository_analysis",
                objective="Identify relevant modules and conventions",
                expected_output_schema="repository_analysis.v1",
                required_skills=["repository-inspection"],
                required_tool_classes={"repository_read"},
                prohibited_actions={"file_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Relevant files identified",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Implement change",
                capability="implementation",
                objective=request_text,
                dependencies=["T-001"],
                expected_output_schema="implementation_result.v1",
                required_tool_classes={
                    "repository_read",
                    "repository_write",
                    "git_read",
                    "git_write",
                },
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Change implemented with tests",
                        verification="test_suite",
                    )
                ],
                allowed_path_patterns=["**/*"],
                rationale="Justified broad path scope for fixture-wide code changes",
            ),
            TaskSpec(
                id="T-003",
                title="Independent review",
                capability="independent_review",
                objective="Review the proposed patch",
                dependencies=["T-002"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read", "git_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="Findings cite evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Compose patch",
                capability="composition",
                objective="Produce final proposed.patch",
                dependencies=["T-002", "T-003"],
                expected_output_schema="composition_result.v1",
                required_tool_classes={"git_read", "artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Patch artifact produced",
                        verification="artifact_check",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(logical_name="proposed.patch", composer_task_id="T-004")
        ],
        validation_strategy="deterministic then independent review",
        risk_classification="low",
    )
    risk_terms = {
        "auth",
        "security",
        "permission",
        "secret",
        "migration",
        "database",
        "payment",
        "concurrency",
        "encryption",
    }
    high_risk = any(term in request_text.lower() for term in risk_terms)
    if not high_risk:
        implementation = proposal.tasks[1].model_copy(update={"dependencies": []})
        composition = proposal.tasks[3].model_copy(
            update={"dependencies": [implementation.id]}
        )
        proposal = proposal.model_copy(
            update={
                "tasks": [implementation, composition],
                "validation_strategy": "deterministic behavioral validation",
                "risk_classification": "low",
            }
        )
    else:
        proposal = proposal.model_copy(update={"risk_classification": "high"})
    return proposal


def default_architecture_plan(request_text: str) -> PlannerOutput:
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Gather requirements",
                capability="requirements",
                objective="Clarify requirements and assumptions",
                expected_output_schema="requirements.v1",
                required_tool_classes={"repository_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Requirements captured",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Draft architecture",
                capability="architecture",
                objective="Produce architecture sections",
                dependencies=["T-001"],
                expected_output_schema="architecture_partial.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Architecture draft created",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-003",
                title="Compose ARCHITECTURE.md",
                capability="composition",
                objective="Compose final architecture document",
                dependencies=["T-002"],
                expected_output_schema="architecture_doc.v1",
                required_tool_classes={"artifact_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="ARCHITECTURE.md complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Independent review",
                capability="independent_review",
                objective="Review architecture for gaps",
                dependencies=["T-003"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Review complete",
                        verification="evidence_check",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(logical_name="ARCHITECTURE.md", composer_task_id="T-003")
        ],
        validation_strategy="section checks then review",
        risk_classification="low",
    )


def extract_unified_diff(text: str) -> str:
    """Pull a unified diff out of model output (raw or fenced)."""
    if not text:
        return ""
    cleaned = text.strip()
    if "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            body = part.strip()
            if body.startswith("diff") or body.startswith("--- ") or "\n+++ " in body:
                # strip optional language tag
                lines = body.splitlines()
                if lines and (
                    lines[0].strip().lower() in {"diff", "patch"}
                    or not lines[0].startswith(("diff --git", "--- ", "+++ "))
                ):
                    body = "\n".join(lines[1:]).strip()
                if body.startswith("diff --git") or body.startswith("--- "):
                    return body
    if "diff --git" in cleaned:
        idx = cleaned.index("diff --git")
        return cleaned[idx:].strip()
    if cleaned.startswith("--- ") or "\n+++ " in cleaned:
        return cleaned
    return ""


def deterministic_impl_files(request_text: str, *, task_objective: str = "") -> list[tuple[str, str]]:
    """
    Request-aware offline/mock implementation.

    Keeps the original health vertical-slice behavior when the request asks for
    health, and produces a simple cache helper when the request asks for cache.
    """
    haystack = f"{request_text}\n{task_objective}".lower()
    if "cache" in haystack:
        return [
            (
                "src/app/cache.py",
                (
                    '"""Cache helper module providing a cache interface and in-memory implementation."""\n\n'
                    "from abc import ABC, abstractmethod\n"
                    "from typing import Any, Optional\n\n\n"
                    "class Cache(ABC):\n"
                    '    """Abstract interface for cache implementations."""\n\n'
                    "    @abstractmethod\n"
                    "    def get(self, key: str) -> Optional[Any]:\n"
                    "        ...\n\n"
                    "    @abstractmethod\n"
                    "    def set(self, key: str, value: Any) -> None:\n"
                    "        ...\n\n"
                    "    @abstractmethod\n"
                    "    def delete(self, key: str) -> None:\n"
                    "        ...\n\n\n"
                    "class InMemoryCache(Cache):\n"
                    '    """Simple in-memory cache implementation backed by a dict."""\n\n'
                    "    def __init__(self) -> None:\n"
                    "        self._store: dict[str, Any] = {}\n\n"
                    "    def get(self, key: str) -> Optional[Any]:\n"
                    "        return self._store.get(key)\n\n"
                    "    def set(self, key: str, value: Any) -> None:\n"
                    "        self._store[key] = value\n\n"
                    "    def delete(self, key: str) -> None:\n"
                    "        self._store.pop(key, None)\n"
                ),
            ),
            (
                "tests/test_cache.py",
                (
                    "from app.cache import InMemoryCache\n\n\n"
                    "def test_in_memory_cache_roundtrip():\n"
                    "    cache = InMemoryCache()\n"
                    '    cache.set("a", 1)\n'
                    '    assert cache.get("a") == 1\n'
                    '    cache.delete("a")\n'
                    '    assert cache.get("a") is None\n'
                ),
            ),
        ]
    if "logging" in haystack:
        return [
            (
                "src/app/logging_util.py",
                (
                    '"""Structured logging helpers."""\n\n'
                    "import json\n"
                    "import logging\n"
                    "from typing import Any\n\n\n"
                    "def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:\n"
                    '    logger.info(json.dumps({"event": event, **fields}, sort_keys=True))\n'
                ),
            )
        ]
    if "retry" in haystack or "jitter" in haystack:
        return [
            (
                "src/app/retry.py",
                (
                    '"""Bounded retry decorator with jitter."""\n\n'
                    "import random\n"
                    "import time\n"
                    "from collections.abc import Callable\n"
                    "from functools import wraps\n"
                    "from typing import ParamSpec, TypeVar\n\n"
                    "P = ParamSpec('P')\n"
                    "R = TypeVar('R')\n\n\n"
                    "def retry(attempts: int = 3, base_delay: float = 0.01):\n"
                    "    def decorate(fn: Callable[P, R]) -> Callable[P, R]:\n"
                    "        @wraps(fn)\n"
                    "        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:\n"
                    "            for attempt in range(attempts):\n"
                    "                try:\n"
                    "                    return fn(*args, **kwargs)\n"
                    "                except Exception:\n"
                    "                    if attempt + 1 == attempts:\n"
                    "                        raise\n"
                    "                    time.sleep(base_delay * (2**attempt) * random.uniform(0.5, 1.5))\n"
                    "            raise AssertionError('unreachable')\n"
                    "        return wrapped\n"
                    "    return decorate\n"
                ),
            )
        ]
    # Default vertical-slice: health endpoint (preserves existing mock tests).
    return [
        (
            "src/app/health.py",
            (
                '"""Health check endpoint."""\n\n'
                "def health() -> dict[str, str]:\n"
                '    return {"status": "ok"}\n'
            ),
        ),
        (
            "tests/test_health.py",
            (
                "from app.health import health\n\n"
                "def test_health():\n"
                '    assert health()["status"] == "ok"\n'
            ),
        ),
    ]


def transitive_dependencies(plan: CompiledPlan, task_id: str) -> set[str]:
    """Return all direct and indirect task dependencies."""
    found: set[str] = set()
    pending = list(plan.tasks[task_id].dependencies)
    while pending:
        dependency = pending.pop()
        if dependency in found or dependency not in plan.tasks:
            continue
        found.add(dependency)
        pending.extend(plan.tasks[dependency].dependencies)
    return found


class RunCoordinator:
    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        data_dir: Path | None = None,
        use_deterministic_planner: bool = False,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.allow_deterministic_workers = isinstance(gateway, MockGateway)
        self.use_deterministic_planner = use_deterministic_planner or isinstance(
            gateway, MockGateway
        )
        self.pf_root = data_dir or (config.root / ".product-factory")
        self.pf_root.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.pf_root / "data" / "product_factory.sqlite")
        self.skills = SkillRegistry.load(config.root / "skills")
        self.tool_registry = default_tool_registry()
        if not isinstance(self.gateway, InstrumentedModelGateway):
            # Will be rebound per-run with a recorder; keep raw gateway reference.
            self._raw_gateway = self.gateway
        else:
            self._raw_gateway = self.gateway.inner

    def run(self, request: RunRequest) -> RunManifest:
        run_id = f"run-{uuid.uuid4().hex[:12]}"
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

        events = EventLog(run_dir / "events.jsonl")
        artifacts = ArtifactStore(run_dir / "artifacts")
        otel = maybe_create_otel_bridge()
        recorder = TelemetryRecorder(
            self.db,
            jsonl=events,
            content_dir=run_dir / "content",
            otel_exporter=otel,
        )
        self.gateway = InstrumentedModelGateway(
            self._raw_gateway, recorder=recorder, db=self.db
        )
        recorder.emit(
            run_id=run_id,
            event_type="run.started",
            summary="Run started",
            payload={"workflow": request.workflow_type},
        )

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

        usage = UsageMetrics()
        base_commit: str | None = None
        repo_summary: dict[str, Any] | None = None
        worktrees: WorktreeManager | None = None
        original_repo: Path | None = None

        try:
            if request.repository_path is not None:
                snap = snapshot_repository(
                    request.repository_path,
                    allow_dirty=self.config.policies.allow_dirty_repo,
                    output_dir=run_dir / "input",
                )
                base_commit = snap.base_commit
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
            proposal = self._plan(run_id, request, repo_summary)
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
                    )
                    compile_result = compile_plan(
                        proposal,
                        max_tasks=request.budget.max_tasks,
                        max_parallel_tasks=request.budget.max_parallel_tasks,
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

            # Execute
            manifest = self._execute(
                run_id=run_id,
                request=request,
                plan=plan,
                run_dir=run_dir,
                artifacts=artifacts,
                events=events,
                recorder=recorder,
                usage=usage,
                worktrees=worktrees,
                original_repo=original_repo,
                base_commit=base_commit or "",
            )
            return manifest
        except (PlanRejectedError, BudgetExhaustedError, ApprovalBlockedError):
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

    def _plan(
        self,
        run_id: str,
        request: RunRequest,
        repo_summary: dict[str, Any] | None,
        repair_errors: list[dict[str, Any]] | None = None,
    ) -> PlannerOutput:
        planner_mode = str(request.metadata.get("planner_mode") or "").strip().lower()
        force_fixed = planner_mode in {"fixed", "complexity_sensitive", "deterministic"}
        force_live = planner_mode == "live"
        use_deterministic = (
            force_fixed
            or (self.use_deterministic_planner and not force_live)
        )
        if use_deterministic:
            if request.workflow_type == "architecture":
                proposal = default_architecture_plan(request.request_text)
            else:
                proposal = default_code_change_plan(request.request_text)
        else:
            proposal = plan_with_gateway(
                self.gateway,
                run_id=run_id,
                request_text=request.request_text,
                workflow_type=request.workflow_type,
                repository_summary=repo_summary,
                budget=request.budget.model_dump(mode="json"),
                repair_errors=repair_errors,
                seed=(
                    int(request.metadata["benchmark_seed"])
                    if request.metadata.get("benchmark_seed") is not None
                    else None
                ),
            )
        if request.workflow_type != "code_change":
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
                        "dependencies": [
                            dep for dep in task.dependencies if dep not in review_ids
                        ]
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
        run_id: str,
        request: RunRequest,
        plan: CompiledPlan,
        run_dir: Path,
        artifacts: ArtifactStore,
        events: EventLog,
        recorder: TelemetryRecorder,
        usage: UsageMetrics,
        worktrees: WorktreeManager | None,
        original_repo: Path | None,
        base_commit: str,
    ) -> RunManifest:
        task_status = {tid: "pending" for tid in plan.tasks}
        results: list[TaskResult] = []
        findings: list[Finding] = []
        repair_count = 0
        repair_origins: dict[str, str] = {}
        origin_repair_attempts: dict[str, int] = {}
        no_progress_count = 0
        previous_patch_fp: str | None = None
        previous_finding_ids: list[str] = []
        previous_validation_failures: set[str] = set()
        patch_text = ""
        architecture_md = ""
        validation_results: list[ValidatorResult] = []

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
            if spent >= request.budget.max_cost_usd:
                raise BudgetExhaustedError("Run budget exhausted")

            ready = runnable_tasks(
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
                        raise RuntimeFailureError(
                            "Dependency failed; " + "; ".join(failed)
                        )
                    raise RuntimeFailureError(f"Unsatisfiable dependencies for tasks: {pending}")
                break

            # Execute wave (sequential in-process but status allows concurrency semantics;
            # fan-out tested via multiple ready tasks in one wave).
            wave_results: list[TaskResult] = []
            for task in ready:
                if spent >= request.budget.max_cost_usd:
                    raise BudgetExhaustedError("Run budget exhausted before task")
                task_status[task.id] = "running"
                started_at = datetime.now(UTC).isoformat()
                self.db.upsert_task(
                    run_id=run_id,
                    task_id=task.id,
                    capability=task.capability,
                    status="running",
                    spec=task.model_dump(mode="json"),
                    started_at=started_at,
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
                result = self._execute_task(
                    run_id=run_id,
                    request=request,
                    task=task,
                    run_dir=run_dir,
                    artifacts=artifacts,
                    worktrees=worktrees,
                    original_repo=original_repo,
                    base_commit=base_commit,
                    recorder=recorder,
                    dependency_outputs=[
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
                            "findings": [
                                finding.model_dump(mode="json") for finding in prior.findings
                            ],
                        }
                        for prior in results
                        if prior.task_id in transitive_dependencies(live_plan, task.id)
                    ],
                )
                usage = usage.merge(result.usage)
                spent = usage.estimated_cost_usd
                task_status[task.id] = "success" if result.status == "success" else result.status
                wave_results.append(result)
                results.append(result)
                findings.extend(result.findings)
                recorder.emit(
                    run_id=run_id,
                    event_type="task.completed"
                    if result.status == "success"
                    else "task.failed",
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
                    active_operation=f"task:{task.id}",
                )
                if result.changed_files and "patch" in (result.summary.lower()):
                    pass
                for art in result.artifact_refs:
                    if art.logical_name.endswith(".patch") or art.media_type == "text/x-diff":
                        patch_text = artifacts.get_text(art.sha256)
                    if art.logical_name == "ARCHITECTURE.md":
                        architecture_md = artifacts.get_text(art.sha256)

            # After wave: deterministic validation for implementation outputs
            for result in wave_results:
                if live_plan.tasks[result.task_id].capability in {
                    "implementation",
                    "repair",
                    "composition",
                    "independent_review",
                }:
                    validation_results = self._validate_outputs(
                        request=request,
                        patch_text=patch_text,
                        architecture_md=architecture_md,
                        original_repo=original_repo,
                        task=live_plan.tasks[result.task_id],
                    )
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
                    if (
                        live_plan.tasks[result.task_id].capability == "repair"
                        and not has_blocking_failures(validation_results)
                    ):
                        origin = repair_origins.get(result.task_id)
                        if origin is not None and task_status.get(origin) == "failed":
                            task_status[origin] = "skipped"
                        for finding in blocking_findings:
                            finding.status = "resolved"
                        blocking_findings = []
                    if (
                        result.status != "success"
                        or has_blocking_failures(validation_results)
                        or blocking_findings
                    ) and repair_count < (
                        request.budget.max_total_repair_tasks
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
                                    "max_repair_attempts": (
                                        origin_task.budget.max_repair_attempts
                                    ),
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
                            )
                            repair_limit = (
                                1
                                if result.status != "success"
                                else request.budget.max_task_repairs
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
                                                        rt.id
                                                        if dep == result.task_id
                                                        else dep
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
        if request.workflow_type == "code_change":
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
                (run_dir / "output" / "proposed.patch").write_text(patch_text, encoding="utf-8")
                artifacts.put_text(
                    patch_text,
                    media_type="text/x-diff",
                    logical_name="proposed.patch",
                    created_by_task_id="compose",
                )
        else:
            if not architecture_md:
                architecture_md = self._compose_architecture(request.request_text, findings)
            (run_dir / "output" / "ARCHITECTURE.md").write_text(architecture_md, encoding="utf-8")
            validation_results.append(validate_architecture_document(architecture_md))

        # Approval gate for code changes
        final_status: str
        terminal_failure = (
            any(status == "failed" for status in task_status.values())
            or has_blocking_failures(validation_results)
            or (request.workflow_type == "code_change" and not patch_text.strip())
        )
        if terminal_failure:
            final_status = "failed"
        elif request.workflow_type == "code_change" and request.approval_policy == "manual_apply":
            approval = {
                "run_id": run_id,
                "base_commit": base_commit,
                "patch_ref": "output/proposed.patch",
                "changed_files": self._changed_files_from_patch(patch_text),
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

    def _execute_task(
        self,
        *,
        run_id: str,
        request: RunRequest,
        task: TaskSpec,
        run_dir: Path,
        artifacts: ArtifactStore,
        worktrees: WorktreeManager | None,
        original_repo: Path | None,
        base_commit: str,
        recorder: TelemetryRecorder | None = None,
        dependency_outputs: list[dict[str, Any]] | None = None,
    ) -> TaskResult:
        profile = select_model(task)
        agent_profile = {
            "repository_analysis": "repository_explorer",
            "implementation": "implementation_worker",
            "repair": "implementation_worker",
            "independent_review": "independent_reviewer",
            "composition": "composer",
            "architecture": "composer",
            "requirements": "repository_explorer",
            "security_review": "security_reviewer",
            "test_design": "test_worker",
            "test_execution": "test_worker",
            "documentation": "composer",
        }.get(task.capability, "implementation_worker")

        skills = self.skills.match(capability=task.capability, required_skills=task.required_skills)
        tool_defs = [
            {"name": t.name, "description": t.description, "parameters": t.input_schema}
            for t in self.tool_registry.list()
            if t.tool_class in task.required_tool_classes or not task.required_tool_classes
        ]
        excerpt_root = original_repo
        repository_excerpts: list[dict[str, str]] = []
        context_omissions: list[str] = []
        context_mode = str(request.metadata.get("context_mode") or "targeted").strip().lower()
        if excerpt_root is not None:
            if context_mode in {"file_list_only", "file-list-only", "paths_only"}:
                repository_excerpts, context_omissions = list_repository_paths(excerpt_root)
            else:
                repository_excerpts, context_omissions = select_repository_excerpts(
                    excerpt_root,
                    objective=f"{request.request_text}\n{task.objective}",
                    max_chars=max(4_000, task.budget.max_input_tokens),
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
            package_id=f"pkg-{task.id}",
        )
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
                },
                content=ctx.messages,
                content_logical_name=f"prompt-package-{task.id}",
            )

        wt_path = run_dir / "scratch" / task.id
        wt_path.mkdir(parents=True, exist_ok=True)
        writable = task.capability in {"implementation", "repair", "test_design", "composition"}
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
            }:
                try:
                    wt = worktrees.get(task.id)
                except KeyError:
                    wt = worktrees.create(task.id, base_commit=base_commit, writable=writable)
                wt_path = wt.path
                if task.capability in {
                    "implementation",
                    "repair",
                    "composition",
                    "independent_review",
                }:
                    superseded = (
                        {
                            predecessor
                            for dependency in dependency_outputs or []
                            for predecessor in dependency.get("dependencies", [])
                        }
                    )
                    owned_paths: dict[str, str] = {}
                    for dependency in dependency_outputs or []:
                        if dependency.get("task_id") in superseded:
                            continue
                        for ref in dependency.get("artifact_refs", []):
                            if ref.get("media_type") != "text/x-diff":
                                continue
                            sha256 = str(ref.get("sha256", ""))
                            if not sha256 or sha256 in inherited_artifacts:
                                continue
                            predecessor_patch = artifacts.get_text(sha256)
                            writer_id = str(dependency.get("task_id") or "unknown")
                            conflicts = detect_writer_conflicts(
                                owned_paths,
                                changed_paths_from_patch(predecessor_patch),
                                writer_id,
                            )
                            if conflicts:
                                lineage_conflicts.extend(conflicts)
                                continue
                            if not apply_patch_check(wt_path, predecessor_patch):
                                lineage_conflicts.append(
                                    {
                                        "path": "(apply)",
                                        "owner_task_id": writer_id,
                                        "conflicting_task_id": task.id,
                                        "reason": "patch_apply_conflict",
                                    }
                                )
                                continue
                            apply_patch(wt_path, predecessor_patch)
                            inherited_artifacts.append(sha256)
                    if inherited_artifacts:
                        try:
                            current = create_patch(wt_path, base_commit)
                            pre_patch_fingerprint = (
                                patch_fingerprint(current) if current.strip() else None
                            )
                        except ValidationFailureError:
                            pre_patch_fingerprint = None
                    (run_dir / "output" / f"{task.id}-lineage.json").write_text(
                        json.dumps(
                            {
                                "task_id": task.id,
                                "base_commit": base_commit,
                                "dependencies": task.dependencies,
                                "inherited_artifact_sha256": inherited_artifacts,
                                "pre_patch_fingerprint": pre_patch_fingerprint,
                                "conflicts": lineage_conflicts,
                            },
                            indent=2,
                        ),
                        encoding="utf-8",
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

        broker = ToolBroker(
            registry=self.tool_registry,
            artifact_store=artifacts,
            worktree_root=wt_path if original_repo else None,
            original_repo=original_repo,
            registered_commands=self.config.policies.registered_commands,
            base_commit=base_commit or None,
            observer=_tool_observer if recorder is not None else None,
        )
        granted = {
            t.name
            for t in self.tool_registry.list()
            if t.tool_class in task.required_tool_classes
            or (not task.required_tool_classes and t.risk_class in {"R0", "R1"})
        }
        # Always allow artifact write for composition/architecture
        if task.capability in {"composition", "architecture", "documentation"}:
            granted.add("write_artifact")
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
                max_calls=max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10),
            )
        )

        if lineage_conflicts and task.capability == "composition":
            return TaskResult(
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

        # Deterministic worker behaviors for MVP vertical slice / mock path
        changed_files: list[str] = []
        artifact_refs = []
        task_findings: list[Finding] = []
        summary = ""
        result_status = "success"
        tool_call_ids: list[str] = []
        model_usage = UsageMetrics()

        if task.capability == "repository_analysis" and broker.worktree_root:
            listing = broker.execute(
                task_id=task.id,
                tool_name="list_files",
                arguments={"directory": ".", "glob": "**/*"},
            )
            tool_call_ids.append(listing["tool_call_id"])
            listed_paths = [
                str(entry.get("path", ""))
                if isinstance(entry, dict)
                else str(entry)
                for entry in listing.get("files", [])
            ]
            report = {
                "files": listed_paths[:50],
                "languages": sorted(
                    {
                        Path(path).suffix.lstrip(".")
                        for path in listed_paths
                        if Path(path).suffix
                    }
                ),
                "entry_points": [
                    path
                    for path in listed_paths
                    if Path(path).name
                    in {"main.py", "app.py", "cli.py", "index.ts", "package.json"}
                ][:20],
                "tests": [
                    path
                    for path in listed_paths
                    if "test" in Path(path).name.lower()
                ][:20],
                "configuration": [
                    path
                    for path in listed_paths
                    if Path(path).name
                    in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
                ][:20],
                "relevant_excerpts": repository_excerpts,
                "conventions": "Derived from repository paths and targeted excerpts",
            }
            art = artifacts.put_json(
                report, logical_name="repository-analysis.json", created_by_task_id=task.id
            )
            shutil.copy(
                artifacts.blobs / art.sha256, run_dir / "output" / "repository-analysis.json"
            )
            artifact_refs.append(art)
            summary = "Repository analyzed"
        elif task.capability in {"implementation", "repair"} and broker.worktree_root:
            applied = False
            # Live path: bounded inspect/edit/test tool loop.
            if not self.allow_deterministic_workers:
                patch_text = ""
                try:
                    impl_messages = [
                        CanonicalMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
                        for m in ctx.messages
                    ]
                    impl_messages.append(
                        CanonicalMessage(
                            role="user",
                            content=(
                                "Implement the task now. Inspect relevant repository files before "
                                "editing. Use the provided tools to modify the worktree and inspect "
                                "the final diff. Finish with a concise summary after the worktree "
                                "contains the complete change."
                            ),
                        )
                    )
                    canonical_tools = [
                        CanonicalToolDefinition(
                            name=definition.name,
                            description=definition.description,
                            parameters=definition.input_schema,
                        )
                        for definition in self.tool_registry.list()
                        if definition.name in granted
                    ]
                    loop = run_tool_agent(
                        gateway=self.gateway,
                        broker=broker,
                        run_id=run_id,
                        task_id=task.id,
                        session_id=f"pf:{run_id}:{profile}:{task.id}",
                        model_profile=profile,
                        messages=impl_messages,
                        tools=canonical_tools,
                        max_rounds=min(16, task.budget.max_tool_calls + 1),
                        max_tool_calls=task.budget.max_tool_calls,
                        max_cost_usd=task.budget.max_cost_usd,
                        max_input_tokens=task.budget.max_input_tokens,
                        max_output_tokens=task.budget.max_output_tokens,
                        timeout_seconds=task.budget.max_wall_clock_seconds,
                        seed=(
                            int(request.metadata["benchmark_seed"])
                            if request.metadata.get("benchmark_seed") is not None
                            else None
                        ),
                    )
                    model_usage = model_usage.merge(loop.usage)
                    tool_call_ids.extend(loop.tool_call_ids)
                    artifact_refs.append(
                        artifacts.put_json(
                            loop.model_dump(mode="json"),
                            logical_name=f"agent-loop-{task.id}.json",
                            created_by_task_id=task.id,
                        )
                    )
                    patch_text = extract_unified_diff(loop.final_text)
                    # The agent may either edit through tools or return a final patch.
                    if patch_text and not any(
                        tc.tool_name in {"create_file", "apply_patch"} for tc in broker.history
                    ):
                        out = broker.execute(
                            task_id=task.id,
                            tool_name="apply_patch",
                            arguments={"patch": patch_text},
                        )
                        tool_call_ids.append(out["tool_call_id"])
                        changed_files.extend(self._changed_files_from_patch(patch_text))
                    diff_probe = broker.execute(
                        task_id=task.id, tool_name="git_diff", arguments={}
                    )
                    tool_call_ids.append(diff_probe["tool_call_id"])
                    applied = bool((diff_probe.get("patch") or "").strip())
                    if applied:
                        changed_files.extend(diff_probe.get("changed_files") or [])
                        summary = (
                            f"Implementation agent completed in {loop.rounds} rounds "
                            f"({loop.termination_reason})"
                        )
                    else:
                        summary = (
                            "invalid_patch_format"
                            if loop.final_text.strip()
                            else (
                                loop.termination_reason
                                if loop.termination_reason
                                in {
                                    "budget_exhausted",
                                    "token_budget_exhausted",
                                    "tool_budget_exhausted",
                                    "no_progress",
                                    "timeout",
                                }
                                else "empty_model_output"
                            )
                        )
                except Exception as exc:
                    reason = "patch_apply_failed" if patch_text else "provider_failed"
                    summary = f"{reason}: {exc}"

            is_offline = self.allow_deterministic_workers
            # Deterministic implementations are test fixtures, never a live fallback.
            if not applied and is_offline:
                for rel_path, content in deterministic_impl_files(
                    request.request_text, task_objective=task.objective
                ):
                    try:
                        out = broker.execute(
                            task_id=task.id,
                            tool_name="create_file",
                            arguments={
                                "path": rel_path,
                                "content": content,
                                "overwrite": True,
                            },
                        )
                        tool_call_ids.append(out["tool_call_id"])
                        changed_files.append(rel_path)
                    except Exception as exc:
                        summary = f"Implementation write failed: {exc}"
                summary = summary or "Implementation files written (deterministic)"
            elif not applied:
                result_status = "failed"
                summary = summary or "empty_model_output"

            grant = broker.grants.get(task.id)
            if grant is not None:
                grant.max_calls = max(grant.max_calls, grant.calls_made + 3)
            try:
                diff = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
                tool_call_ids.append(diff["tool_call_id"])
                patch_body = diff.get("patch") or ""
                changed_from_diff = diff.get("changed_files") or []
                artifact_sha = diff.get("artifact_sha256")
            except Exception:
                patch_body = (
                    create_patch(broker.worktree_root, base_commit)
                    if broker.worktree_root and base_commit
                    else ""
                )
                changed_from_diff = [
                    line[6:] for line in patch_body.splitlines() if line.startswith("+++ b/")
                ]
                artifact_sha = None
            if patch_body.strip():
                art = artifacts.put_text(
                    patch_body,
                    media_type="text/x-diff",
                    logical_name="implementation.patch",
                    created_by_task_id=task.id,
                )
                if artifact_sha is None:
                    artifact_sha = art.sha256
                artifact_refs.append(art)
                (run_dir / "output" / "implementation.patch").write_text(
                    patch_body, encoding="utf-8"
                )
                if not changed_files:
                    changed_files.extend(changed_from_diff)
                lineage_path = run_dir / "output" / f"{task.id}-lineage.json"
                if lineage_path.exists():
                    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
                    lineage["post_patch_fingerprint"] = patch_fingerprint(patch_body)
                    lineage_path.write_text(
                        json.dumps(lineage, indent=2), encoding="utf-8"
                    )
            elif not is_offline:
                result_status = "failed"
                summary = summary or "invalid_patch_format"
            summary = summary or "Implementation patch produced"
        elif task.capability == "independent_review":
            if broker.worktree_root is not None:
                diff = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
                tool_call_ids.append(diff["tool_call_id"])
                review_patch = diff.get("patch") or ""
                resource_type = "patch"
            else:
                review_patch = request.request_text
                for dependency in dependency_outputs or []:
                    for ref in dependency.get("artifact_refs", []):
                        if ref.get("media_type") == "text/markdown" and ref.get("sha256"):
                            review_patch = artifacts.get_text(str(ref["sha256"]))
                            break
                resource_type = "task_result"
            patch_ref = ResourceRef(
                id=f"patch:{task.id}",
                resource_type=resource_type,
                origin="task",
                scope="review_input",
                trust_level="mixed",
                content_hash=patch_fingerprint(review_patch),
            )
            if resource_type == "patch" and review_patch:
                artifact_refs.append(
                    artifacts.put_text(
                        review_patch,
                        media_type="text/x-diff",
                        logical_name=f"review-input-{task.id}.patch",
                        created_by_task_id=task.id,
                    )
                )
            if self.allow_deterministic_workers:
                task_findings.append(
                    Finding(
                        id=f"F-{task.id}",
                        category="correctness",
                        severity="minor",
                        summary="No blocking issues detected",
                        explanation="Deterministic mock reviewer inspected the inherited patch.",
                        evidence_refs=[patch_ref],
                        confidence=0.6,
                        produced_by=profile,
                        status="resolved",
                    )
                )
            else:
                review_messages = [
                    CanonicalMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
                    for m in ctx.messages
                ]
                review_messages.append(
                    CanonicalMessage(
                        role="user",
                        content=(
                            "Review this patch independently. Return JSON with a findings array. "
                            "Each finding must contain category, severity, summary, explanation, "
                            "recommended_action, confidence, and evidence_path. Return an empty "
                            f"array when there are no issues.\n\n{review_patch}"
                        ),
                    )
                )
                response = self.gateway.complete(
                    ModelRequest(
                        request_id=f"review-{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        task_id=task.id,
                        session_id=f"pf:{run_id}:{profile}:{task.id}",
                        model_profile=profile,
                        messages=review_messages,
                        output_schema={
                            "type": "object",
                            "properties": {
                                "findings": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "category": {"type": "string"},
                                            "severity": {
                                                "type": "string",
                                                "enum": ["blocking", "major", "minor"],
                                            },
                                            "summary": {"type": "string"},
                                            "explanation": {"type": "string"},
                                            "recommended_action": {"type": "string"},
                                            "confidence": {"type": "number"},
                                            "evidence_path": {
                                                "type": "string",
                                                "minLength": 1,
                                            },
                                        },
                                        "required": [
                                            "category",
                                            "severity",
                                            "summary",
                                            "explanation",
                                            "recommended_action",
                                            "confidence",
                                            "evidence_path",
                                        ],
                                        "additionalProperties": False,
                                    },
                                }
                            },
                            "required": ["findings"],
                            "additionalProperties": False,
                        },
                        max_output_tokens=3000,
                        seed=(
                            int(request.metadata["benchmark_seed"])
                            if request.metadata.get("benchmark_seed") is not None
                            else None
                        ),
                    )
                )
                model_usage = model_usage.merge(response.usage)
                payload = response.structured_data
                if payload is None and response.text:
                    payload = json.loads(response.text)
                if response.status != "success" or payload is None:
                    result_status = "failed"
                    summary = f"review_{response.status}"
                else:
                    for index, raw in enumerate(payload.get("findings", []), 1):
                        evidence_path = str(raw.get("evidence_path") or "").strip()
                        evidence_ok = False
                        if evidence_path:
                            if evidence_path in review_patch or f"b/{evidence_path}" in review_patch:
                                evidence_ok = True
                            elif broker.worktree_root is not None:
                                candidate = (broker.worktree_root / evidence_path)
                                try:
                                    candidate.resolve().relative_to(
                                        broker.worktree_root.resolve()
                                    )
                                    evidence_ok = candidate.exists()
                                except ValueError:
                                    evidence_ok = False
                        evidence = patch_ref.model_copy(
                            update={"scope": evidence_path or "patch"}
                        )
                        confidence = float(raw["confidence"])
                        severity = str(raw["severity"]).lower()
                        if severity not in {"blocking", "major", "minor"}:
                            severity = "major"
                        category = str(raw["category"]).strip().lower().replace(" ", "_")
                        if category == "testgap":
                            category = "test_gap"
                        if category not in {
                            "correctness",
                            "security",
                            "maintainability",
                            "test_gap",
                            "architecture",
                            "requirements",
                            "policy",
                            "evidence",
                            "tool_error",
                        }:
                            category = "correctness"
                        if not evidence_ok:
                            if severity == "blocking":
                                severity = "major"
                            confidence = min(confidence, 0.49)
                        if severity == "blocking" and confidence < 0.7:
                            severity = "major"
                        criterion_id = None
                        for ac in task.acceptance_criteria:
                            criterion_id = ac.id
                            break
                        for ac in task.acceptance_criteria:
                            if ac.id in evidence_path or ac.id in str(raw.get("summary", "")):
                                criterion_id = ac.id
                                break
                        task_findings.append(
                            Finding(
                                id=f"F-{task.id}-{index}",
                                criterion_id=criterion_id,
                                category=category,  # type: ignore[arg-type]
                                severity=severity,  # type: ignore[arg-type]
                                summary=raw["summary"],
                                explanation=raw["explanation"],
                                evidence_refs=[evidence],
                                recommended_action=raw["recommended_action"],
                                confidence=confidence,
                                produced_by=profile,
                            )
                        )
            art = artifacts.put_json(
                [finding.model_dump(mode="json") for finding in task_findings],
                logical_name="review-findings.json",
                created_by_task_id=task.id,
            )
            shutil.copy(artifacts.blobs / art.sha256, run_dir / "output" / "review-findings.json")
            artifact_refs.append(art)
            summary = summary or "Independent review complete"
        elif task.capability == "composition":
            if request.workflow_type == "architecture":
                architecture_md = self._compose_architecture(request.request_text, [])
                art = artifacts.put_text(
                    architecture_md,
                    media_type="text/markdown",
                    logical_name="ARCHITECTURE.md",
                    created_by_task_id=task.id,
                )
                artifact_refs.append(art)
                summary = "Architecture composed"
            else:
                if broker.worktree_root and base_commit:
                    # Composition is the deterministic diff of its inherited lineage.
                    patch = create_patch(broker.worktree_root, base_commit)
                    art = artifacts.put_text(
                        patch,
                        media_type="text/x-diff",
                        logical_name="proposed.patch",
                        created_by_task_id=task.id,
                    )
                    artifact_refs.append(art)
                    summary = "Patch composed" if patch.strip() else "Empty patch composed"
                    if not patch.strip():
                        result_status = "failed"
                    lineage_path = run_dir / "output" / f"{task.id}-lineage.json"
                    if lineage_path.exists():
                        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
                        lineage["final_patch_fingerprint"] = (
                            patch_fingerprint(patch) if patch else None
                        )
                        lineage["post_patch_fingerprint"] = lineage["final_patch_fingerprint"]
                        lineage_path.write_text(
                            json.dumps(lineage, indent=2), encoding="utf-8"
                        )
                else:
                    summary = "Nothing to compose"
        elif task.capability in {"architecture", "requirements"}:
            art = artifacts.put_json(
                {"objective": task.objective, "notes": "draft"},
                logical_name=f"{task.id}.json",
                created_by_task_id=task.id,
            )
            artifact_refs.append(art)
            summary = f"{task.capability} draft created"
        else:
            summary = f"Task {task.capability} completed (stub)"

        # Optional generic model invocation for non-mock when useful
        if (
            not isinstance(self.gateway, MockGateway)
            and task.capability in {"architecture", "requirements"}
            and not self.use_deterministic_planner
        ):
            try:
                resp = self.gateway.complete(
                    ModelRequest(
                        request_id=f"req-{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        task_id=task.id,
                        session_id=f"pf:{run_id}:{profile}:{task.id}",
                        model_profile=profile,
                        messages=[
                            CanonicalMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
                            for m in ctx.messages
                        ],
                        max_output_tokens=4000,
                        seed=(
                            int(request.metadata["benchmark_seed"])
                            if request.metadata.get("benchmark_seed") is not None
                            else None
                        ),
                    )
                )
                model_usage = model_usage.merge(resp.usage)
            except Exception:
                pass

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
        result = TaskResult(
            task_id=task.id,
            status=result_status,
            summary=summary,
            artifact_refs=artifact_refs,
            evidence_refs=context_evidence,
            findings=task_findings,
            changed_files=changed_files,
            model_profile=profile,
            resolved_model_id=profile,
            provider=getattr(self.gateway, "default_model", type(self.gateway).__name__),
            prompt_package_hash=ctx.package_hash,
            tool_call_ids=tool_call_ids,
            usage=model_usage,
        )
        self.db.upsert_task(
            run_id=run_id,
            task_id=task.id,
            capability=task.capability,
            status=result_status,
            spec=task.model_dump(mode="json"),
            result=result.model_dump(mode="json"),
            ended_at=datetime.now(UTC).isoformat(),
            active_operation=None,
        )
        for art in artifact_refs:
            self.db.record_artifact(art.model_dump(mode="json"))
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

    def _validate_outputs(
        self,
        *,
        request: RunRequest,
        patch_text: str,
        architecture_md: str,
        original_repo: Path | None,
        task: TaskSpec,
    ) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        if request.workflow_type == "code_change" and patch_text and original_repo:
            results.append(validate_patch_applies(original_repo, patch_text))
            changed = self._changed_files_from_patch(patch_text)
            results.append(validate_path_scope(changed, task.allowed_path_patterns))
            results.append(validate_secrets(patch_text))
            smoke_commands = [
                value
                for value in request.metadata.get("smoke_commands", "").split(",")
                if value
            ]
            results.extend(
                validate_behavioral_commands(
                    repository=original_repo,
                    patch=patch_text,
                    command_ids=smoke_commands,
                    registered_commands=self.config.policies.registered_commands,
                )
            )
        if request.workflow_type == "architecture" and architecture_md:
            results.append(validate_architecture_document(architecture_md))
            results.append(validate_secrets(architecture_md))
        return results

    def _changed_files_from_patch(self, patch: str) -> list[str]:
        files = []
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                files.append(line[6:])
        return files

    def _compose_architecture(self, request_text: str, findings: list[Finding]) -> str:
        sections = [
            "# ARCHITECTURE.md",
            "",
            "## Objective",
            request_text.strip() or "TBD",
            "",
            "## Scope",
            "MVP scope as requested.",
            "",
            "## Assumptions",
            "- Standard web service deployment.",
            "",
            "## Functional requirements",
            "- Deliver the requested capabilities.",
            "",
            "## Nonfunctional requirements",
            "- Reliability, observability, and security baselines.",
            "",
            "## System context",
            "Users interact via API/CLI; system persists to a database.",
            "",
            "## Components and responsibilities",
            "- API layer, domain services, persistence.",
            "",
            "## Data flows",
            "```mermaid",
            "flowchart LR",
            "  User --> API --> Service --> DB",
            "```",
            "",
            "## External dependencies",
            "- Managed database; optional object storage.",
            "",
            "## Security boundaries",
            "- Authn/authz at API edge; secrets outside repo.",
            "",
            "## Failure handling",
            "- Timeouts, retries with backoff, graceful degradation.",
            "",
            "## Observability",
            "- Structured logs, metrics, traces.",
            "",
            "## Testing strategy",
            "- Unit, contract, and integration tests.",
            "",
            "## Deployment assumptions",
            "- Single-region container deploy for MVP.",
            "",
            "## Trade-offs",
            "- Simplicity over premature distribution.",
            "",
            "## Rejected alternatives",
            "- Multi-region active-active for MVP.",
            "",
            "## Open questions",
            "- Exact SLA targets.",
            "",
            "## Implementation stages",
            "1. Scaffold 2. Core API 3. Hardening",
            "",
            "## Acceptance criteria",
            "- Document sections complete; open questions listed.",
            "",
        ]
        if findings:
            sections.append("## Review findings")
            for f in findings:
                sections.append(f"- {f.summary}")
        return "\n".join(sections) + "\n"

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
