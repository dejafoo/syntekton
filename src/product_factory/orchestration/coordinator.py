"""Run coordinator — end-to-end orchestration without provider-specific logic."""

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
from product_factory.connectors.defaults import default_connector_registry
from product_factory.context.assembler import (
    assemble_context,
    list_repository_paths,
    select_repository_excerpts,
)
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
from product_factory.orchestration.budget_ledger import BudgetLedger, warn_unused_profile_set
from product_factory.orchestration.concurrency import run_wave
from product_factory.orchestration.repair import (
    create_repair_tasks,
    patch_fingerprint,
    should_terminate_no_progress,
    update_no_progress,
)
from product_factory.orchestration.review_findings import (
    parse_raw_findings,
    validate_review_findings,
)
from product_factory.orchestration.skill_grants import enforce_skill_grants
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
from product_factory.scheduling.scheduler import resolve_task_model_profile, runnable_tasks
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import default_tool_registry
from product_factory.validation.pipeline import (
    ARCHITECTURE_REQUIRED_SECTIONS,
    has_blocking_failures,
    validate_architecture_document,
    validate_architecture_request_specificity,
    validate_behavioral_commands,
    validate_citations,
    validate_document_sections,
    validate_investigation_document,
    validate_patch_applies,
    validate_path_scope,
    validate_secrets,
)
from product_factory.workflows.artifacts import (
    ROLE_ARCHITECTURE_DOCUMENT,
    ROLE_EVIDENCE_REPORT,
    ROLE_PROPOSED_PATCH,
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
    ArtifactLandMap,
)
from product_factory.workflows.base import WorkflowPack
from product_factory.workflows.quality_gate import (
    QUALITY_GATE_REQUIRED_SECTIONS,
    QUALITY_GATE_VALIDATOR_IDS,
)
from product_factory.workflows.registry import land_map_for_request, resolve_workflow_pack

logger = logging.getLogger("product_factory.orchestration.coordinator")

# code_change/repository_change resolve to the same pack (P1.G); architecture/
# technical_plan share the technical_plan pack (P3.D).
_CODE_CHANGE_WORKFLOW_TYPES = frozenset({"code_change", "repository_change"})
_TECHNICAL_PLAN_WORKFLOW_TYPES = frozenset({"architecture", "technical_plan"})
_INVESTIGATION_WORKFLOW_TYPES = frozenset({"repository_investigation"})
_QUALITY_GATE_WORKFLOW_TYPES = frozenset({"quality_gate"})
_PACK_BACKED_WORKFLOW_TYPES = (
    _CODE_CHANGE_WORKFLOW_TYPES
    | _TECHNICAL_PLAN_WORKFLOW_TYPES
    | _INVESTIGATION_WORKFLOW_TYPES
    | _QUALITY_GATE_WORKFLOW_TYPES
)

# Repository mutation tools — never granted for investigation or quality packs.
_REPOSITORY_WRITE_TOOL_NAMES = frozenset({"create_file", "apply_patch"})

# Quality-gate deliverable roles and their fallback names. Keyed by role so a
# composer task resolves its document from the plan, not from the workflow id.
_QUALITY_GATE_ROLES: dict[str, str] = {
    ROLE_TEST_PLAN: "TEST_PLAN.md",
    ROLE_QUALITY_FINDINGS: "QUALITY_FINDINGS.md",
    ROLE_SECURITY_EVIDENCE: "SECURITY_EVIDENCE.md",
}


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
            FinalArtifactSpec(
                logical_name="proposed.patch",
                composer_task_id="T-004",
                role=ROLE_PROPOSED_PATCH,
            )
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
        composition = proposal.tasks[3].model_copy(update={"dependencies": [implementation.id]})
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
            FinalArtifactSpec(
                logical_name="ARCHITECTURE.md",
                composer_task_id="T-003",
                role=ROLE_ARCHITECTURE_DOCUMENT,
            )
        ],
        validation_strategy="section checks then review",
        risk_classification="low",
    )


def default_technical_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for `technical_plan` — same shape as architecture."""
    return default_architecture_plan(request_text)


def default_investigation_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for read-only repository investigation (P3.D)."""
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Inspect repository structure",
                capability="repository_analysis",
                objective="Identify relevant modules, evidence paths, and conventions",
                expected_output_schema="repository_analysis.v1",
                required_skills=["repository-inspection"],
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Relevant files identified with path evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Compose evidence report",
                capability="composition",
                objective="Produce EVIDENCE_REPORT.md with cited paths and assumptions",
                dependencies=["T-001"],
                expected_output_schema="evidence_report.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions={"file_write", "repository_write", "git_write"},
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Evidence report with citations and assumptions",
                        verification="static_rule",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="EVIDENCE_REPORT.md",
                composer_task_id="T-002",
                role=ROLE_EVIDENCE_REPORT,
            )
        ],
        validation_strategy="section checks, citation presence, secret scan",
        risk_classification="low",
    )


def default_quality_gate_plan(request_text: str) -> PlannerOutput:
    """Frozen fixed planner for the `quality_gate` pack (P4.E).

    Three composer tasks, one per land-map role, so each deliverable has a single
    owning task that the coordinator can resolve back to its role. No task may
    write to the repository: the pack reports, it does not change code.
    """
    read_only = {"file_write", "repository_write", "git_write"}
    return PlannerOutput(
        objective=request_text[:200],
        assumptions=[],
        tasks=[
            TaskSpec(
                id="T-001",
                title="Design quality checks",
                capability="test_design",
                objective="Identify risk areas and the checks that would cover them",
                expected_output_schema="test_design.v1",
                required_tool_classes={"repository_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-001",
                        description="Risk areas identified with paths",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-002",
                title="Execute registered validation commands",
                capability="test_execution",
                objective="Run the registered validation commands and capture results",
                dependencies=["T-001"],
                expected_output_schema="test_execution.v1",
                required_tool_classes={"repository_read", "validation_command"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-002",
                        description="Command outcomes captured or explicitly skipped",
                        verification="artifact_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-003",
                title="Security review",
                capability="security_review",
                objective="Review the repository for security-relevant defects",
                dependencies=["T-001"],
                expected_output_schema="security_review.v1",
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-003",
                        description="Security checks recorded with evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-004",
                title="Independent review",
                capability="independent_review",
                objective="Independently review quality with cited evidence",
                dependencies=["T-002", "T-003"],
                expected_output_schema="review_findings.v1",
                required_tool_classes={"repository_read", "git_read"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-004",
                        description="Findings cite file evidence",
                        verification="evidence_check",
                    )
                ],
            ),
            TaskSpec(
                id="T-005",
                title="Compose test plan",
                capability="composition",
                objective="Compose the test plan deliverable",
                dependencies=["T-001"],
                expected_output_schema="test_plan.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-005",
                        description="Test plan sections complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-006",
                title="Compose security evidence",
                capability="composition",
                objective="Compose the security evidence deliverable",
                dependencies=["T-003"],
                expected_output_schema="security_evidence.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-006",
                        description="Security evidence sections complete",
                        verification="static_rule",
                    )
                ],
            ),
            TaskSpec(
                id="T-007",
                title="Compose quality findings",
                capability="composition",
                objective="Compose the quality findings deliverable",
                dependencies=["T-004", "T-005", "T-006"],
                expected_output_schema="quality_findings.v1",
                required_tool_classes={"repository_read", "artifact_write"},
                prohibited_actions=read_only,
                acceptance_criteria=[
                    AcceptanceCriterion(
                        id="AC-007",
                        description="Findings carry evidence and recommended actions",
                        verification="static_rule",
                    )
                ],
            ),
        ],
        final_artifacts=[
            FinalArtifactSpec(
                logical_name="TEST_PLAN.md",
                composer_task_id="T-005",
                role=ROLE_TEST_PLAN,
            ),
            FinalArtifactSpec(
                logical_name="SECURITY_EVIDENCE.md",
                composer_task_id="T-006",
                role=ROLE_SECURITY_EVIDENCE,
            ),
            FinalArtifactSpec(
                logical_name="QUALITY_FINDINGS.md",
                composer_task_id="T-007",
                role=ROLE_QUALITY_FINDINGS,
            ),
        ],
        validation_strategy="section checks, citation presence, secret scan",
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


def deterministic_impl_files(
    request_text: str, *, task_objective: str = ""
) -> list[tuple[str, str]]:
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
    # Default vertical-slice: callable health module (plain package, no HTTP).
    return [
        (
            "src/app/health.py",
            (
                '"""Health check module."""\n\n'
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
        self.connector_registry = default_connector_registry(config.connectors)
        # Connector tools share the one registry so `ToolBroker.execute` resolves
        # and trust-labels them exactly like built-in tools.
        for definition in self.connector_registry.tool_definitions():
            self.tool_registry.register(definition)
        self.connector_broker = ConnectorBroker(
            self.connector_registry,
            config=config.connectors,
            mock=isinstance(gateway, MockGateway),
        )
        if not isinstance(self.gateway, InstrumentedModelGateway):
            # Will be rebound per-run with a recorder; keep raw gateway reference.
            self._raw_gateway = self.gateway
        else:
            self._raw_gateway = self.gateway.inner

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

        events = EventLog(run_dir / "events.jsonl")
        artifacts = ArtifactStore(run_dir / "artifacts")
        otel = maybe_create_otel_bridge()
        recorder = TelemetryRecorder(
            self.db,
            jsonl=events,
            content_dir=run_dir / "content",
            otel_exporter=otel,
        )
        ledger = BudgetLedger(request.budget)
        self.gateway = InstrumentedModelGateway(
            self._raw_gateway, recorder=recorder, db=self.db, ledger=ledger
        )
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
        if request.workflow_type in _PACK_BACKED_WORKFLOW_TYPES:
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            # Bad artifact overrides fail here, before any planning spend.
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

            self._raise_if_cancelled(run_id)

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
                ledger=ledger,
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

        events = EventLog(run_dir / "events.jsonl")
        artifacts = ArtifactStore(run_dir / "artifacts")
        otel = maybe_create_otel_bridge()
        recorder = TelemetryRecorder(
            self.db, jsonl=events, content_dir=run_dir / "content", otel_exporter=otel
        )

        budget_snapshot = json.loads(run_row["budget_json"]) if run_row.get("budget_json") else None
        ledger = (
            BudgetLedger.restore(request.budget, budget_snapshot)
            if budget_snapshot
            else BudgetLedger(request.budget)
        )
        self.gateway = InstrumentedModelGateway(
            self._raw_gateway, recorder=recorder, db=self.db, ledger=ledger
        )
        workflow_pack: WorkflowPack | None = None
        land_map = ArtifactLandMap()
        if request.workflow_type in _PACK_BACKED_WORKFLOW_TYPES:
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            land_map = land_map_for_request(request)

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
                run_id=run_id,
                request=request,
                plan=live_plan,
                run_dir=run_dir,
                artifacts=artifacts,
                events=events,
                recorder=recorder,
                usage=usage,
                worktrees=worktrees,
                original_repo=original_repo,
                base_commit=base_commit or "",
                ledger=ledger,
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
    ) -> PlannerOutput:
        planner_mode = str(request.metadata.get("planner_mode") or "").strip().lower()
        force_fixed = planner_mode in {"fixed", "complexity_sensitive", "deterministic"}
        force_live = planner_mode == "live"
        use_deterministic = force_fixed or (self.use_deterministic_planner and not force_live)
        if use_deterministic:
            if request.workflow_type in _TECHNICAL_PLAN_WORKFLOW_TYPES:
                proposal = default_technical_plan(request.request_text)
            elif request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES:
                proposal = default_investigation_plan(request.request_text)
            elif request.workflow_type in _QUALITY_GATE_WORKFLOW_TYPES:
                proposal = default_quality_gate_plan(request.request_text)
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
        if request.workflow_type not in _CODE_CHANGE_WORKFLOW_TYPES:
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
        ledger: BudgetLedger,
        workflow_pack: WorkflowPack | None = None,
        land_map: ArtifactLandMap | None = None,
        initial_task_status: dict[str, str] | None = None,
        initial_results: list[TaskResult] | None = None,
        initial_patch_text: str = "",
        initial_architecture_md: str = "",
        initial_evidence_report_md: str = "",
        initial_documents_by_role: dict[str, str] | None = None,
    ) -> RunManifest:
        # `initial_*` are only populated by `resume()` (P1.B): they seed the
        # wave loop with already-completed task state so resumed runs incur
        # no new model/tool spend for success/skipped tasks.
        land_map = land_map or ArtifactLandMap()
        architecture_name = land_map.logical_name_for(
            ROLE_ARCHITECTURE_DOCUMENT, default="ARCHITECTURE.md"
        )
        evidence_name = land_map.logical_name_for(
            ROLE_EVIDENCE_REPORT, default="EVIDENCE_REPORT.md"
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
            workflow_pack is not None
            and workflow_pack.validation_policy.get("findings_are_deliverable")
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
            self._raise_if_cancelled(run_id)
            if spent >= request.budget.max_cost_usd:
                raise BudgetExhaustedError("Run budget exhausted")
            ledger.check_wall_clock()

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
                    if prior.task_id in transitive_dependencies(live_plan, task.id)
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
            ) -> TaskResult:
                return self._execute_task(
                    run_id=run_id,
                    request=request,
                    task=task,
                    run_dir=run_dir,
                    artifacts=artifacts,
                    worktrees=worktrees,
                    original_repo=original_repo,
                    base_commit=base_commit,
                    recorder=recorder,
                    ledger=ledger,
                    dependency_outputs=dependency_outputs_by_task[task.id],
                    land_map=land_map,
                    composer_role=composer_roles.get(task.id),
                )

            wave_results: list[TaskResult] = run_wave(
                ready,
                executor_fn=_run_one,
                max_workers=request.budget.max_parallel_tasks,
            )
            self._raise_if_cancelled(run_id)

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
                    if art.media_type == "text/markdown":
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
                    validation_results = self._validate_outputs(
                        request=request,
                        patch_text=patch_text,
                        architecture_md=architecture_md,
                        evidence_report_md=evidence_report_md,
                        original_repo=original_repo,
                        task=live_plan.tasks[result.task_id],
                        findings=result.findings,
                        ledger=ledger,
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
                                registered_command_ids=self._resolve_validation_command_ids(request)
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
        if request.workflow_type in _CODE_CHANGE_WORKFLOW_TYPES:
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
        elif request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES:
            if not evidence_report_md:
                evidence_report_md = self._compose_evidence_report(
                    request.request_text,
                    findings=findings,
                    dependency_outputs=[],
                    document_name=evidence_name,
                )
            (run_dir / "output" / evidence_name).write_text(evidence_report_md, encoding="utf-8")
            validation_results.append(validate_investigation_document(evidence_report_md))
            validation_results.append(validate_citations(evidence_report_md))
            validation_results.append(validate_secrets(evidence_report_md))
        elif request.workflow_type in _QUALITY_GATE_WORKFLOW_TYPES:
            for entry in land_map.entries:
                document = documents_by_role.get(entry.role, "")
                if not document.strip():
                    # An optional deliverable the run never produced is absent, not
                    # empty — `materialize-all` skips what isn't there.
                    if entry.required:
                        validation_results.append(
                            ValidatorResult(
                                validator_id=QUALITY_GATE_VALIDATOR_IDS.get(
                                    entry.role, f"{entry.role}_sections"
                                ),
                                status="fail",
                                message=f"Required deliverable {entry.role} was not produced",
                                details={"logical_name": entry.logical_name},
                            )
                        )
                    continue
                (run_dir / "output" / entry.logical_name).write_text(document, encoding="utf-8")
                validation_results.append(
                    validate_document_sections(
                        document,
                        validator_id=QUALITY_GATE_VALIDATOR_IDS.get(
                            entry.role, f"{entry.role}_sections"
                        ),
                        required_sections=QUALITY_GATE_REQUIRED_SECTIONS.get(entry.role, ()),
                    )
                )
                validation_results.append(validate_secrets(document))
            findings_doc = documents_by_role.get(ROLE_QUALITY_FINDINGS, "")
            if findings_doc.strip():
                validation_results.append(validate_citations(findings_doc))
        else:
            if not architecture_md:
                architecture_md = self._compose_architecture(
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

        # Approval gate for code changes
        final_status: str
        terminal_failure = (
            any(status == "failed" for status in task_status.values())
            or has_blocking_failures(validation_results)
            or (request.workflow_type in _CODE_CHANGE_WORKFLOW_TYPES and not patch_text.strip())
            or (
                request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES
                and not evidence_report_md.strip()
            )
        )
        if terminal_failure:
            final_status = "failed"
        elif (
            request.workflow_type in _CODE_CHANGE_WORKFLOW_TYPES
            and request.approval_policy == "manual_apply"
        ):
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
        ledger: BudgetLedger | None = None,
        land_map: ArtifactLandMap | None = None,
        composer_role: str | None = None,
    ) -> TaskResult:
        land_map = land_map or ArtifactLandMap()
        profile = resolve_task_model_profile(task, metadata=request.metadata)
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
        registered_ids = self._resolve_validation_command_ids(request) or list(
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
                    superseded = {
                        predecessor
                        for dependency in dependency_outputs or []
                        for predecessor in dependency.get("dependencies", [])
                    }
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

        self.connector_broker.set_audit(_connector_audit if recorder is not None else None)

        broker = ToolBroker(
            registry=self.tool_registry,
            artifact_store=artifacts,
            worktree_root=wt_path if original_repo else None,
            original_repo=original_repo,
            registered_commands=self.config.policies.registered_commands,
            base_commit=base_commit or None,
            observer=_tool_observer if recorder is not None else None,
            ledger=ledger,
            connectors=self.connector_broker,
            run_id=run_id,
        )
        granted = {
            t.name
            for t in self.tool_registry.list()
            if t.tool_class in task.required_tool_classes
            or (not task.required_tool_classes and t.risk_class in {"R0", "R1"})
        }
        # Always allow artifact write for composition/architecture/documentation
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
        # Investigation packs never receive repository mutation tools (P3.D).
        if request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES:
            granted -= _REPOSITORY_WRITE_TOOL_NAMES
            granted.discard("run_validation_command")
        elif request.workflow_type in _QUALITY_GATE_WORKFLOW_TYPES:
            # A quality gate may execute registered validation commands, but it
            # reports on code rather than changing it (P4.E).
            granted -= _REPOSITORY_WRITE_TOOL_NAMES

        # Connector grants are resolved last so no capability-specific branch above
        # can hand out an external provider by accident. External tools are never
        # part of the permissive default: a task reaches one only when its pack
        # names the tool class *and* an operator enabled the connector.
        connector_tool_names = self.connector_registry.tool_names()
        if connector_tool_names:
            granted -= connector_tool_names
            granted |= self.connector_broker.grantable_tool_names(task.required_tool_classes)

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
                max_calls=max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10),
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

        # Deterministic worker behaviors for MVP vertical slice / mock path
        changed_files: list[str] = []
        artifact_refs = []
        task_findings: list[Finding] = []
        summary = ""
        result_status = "success"
        tool_call_ids: list[str] = []
        model_usage = UsageMetrics()

        # A quality gate's design task needs the same repository scope as an
        # analysis task: without it the test plan has no real paths to rank.
        scopes_repository = task.capability == "repository_analysis" or (
            task.capability == "test_design"
            and request.workflow_type in _QUALITY_GATE_WORKFLOW_TYPES
        )
        if scopes_repository and broker.worktree_root:
            listing = broker.execute(
                task_id=task.id,
                tool_name="list_files",
                arguments={"directory": ".", "glob": "**/*"},
            )
            tool_call_ids.append(listing["tool_call_id"])
            listed_paths = [
                str(entry.get("path", "")) if isinstance(entry, dict) else str(entry)
                for entry in listing.get("files", [])
            ]
            report = {
                "files": listed_paths[:50],
                "languages": sorted(
                    {Path(path).suffix.lstrip(".") for path in listed_paths if Path(path).suffix}
                ),
                "entry_points": [
                    path
                    for path in listed_paths
                    if Path(path).name
                    in {"main.py", "app.py", "cli.py", "index.ts", "package.json"}
                ][:20],
                "tests": [path for path in listed_paths if "test" in Path(path).name.lower()][:20],
                "configuration": [
                    path
                    for path in listed_paths
                    if Path(path).name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod"}
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
            summary = (
                "Quality scope identified"
                if task.capability == "test_design"
                else "Repository analyzed"
            )
        elif task.capability in {"implementation", "repair"} and broker.worktree_root:
            applied = False
            seeded_defect = str(request.metadata.get("seed_repair_defect") or "").strip()
            force_seeded_impl = (
                task.capability == "implementation"
                and request.metadata.get("force_seeded_impl") == "true"
                and bool(seeded_defect)
            )
            if force_seeded_impl:
                from product_factory.evaluation.defects import (
                    defect_files,
                    resolve_defect_kind,
                )

                case_id = str(request.metadata.get("eval_case") or "")
                kind = resolve_defect_kind(case_id, explicit=seeded_defect)
                for rel_path, content in defect_files(case_id or "code_cache", kind):
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
                applied = True
                summary = f"Seeded repairable defect ({kind})"
                artifact_refs.append(
                    artifacts.put_json(
                        {
                            "seed_repair_defect": kind,
                            "case_id": case_id,
                            "files": changed_files,
                        },
                        logical_name=f"seeded-defect-{task.id}.json",
                        created_by_task_id=task.id,
                    )
                )
            # Live path: bounded inspect/edit/test tool loop.
            elif not self.allow_deterministic_workers:
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
                                + (
                                    " When re-checking tests, call run_validation_command with a "
                                    f"registered command_id only ({', '.join(registered_ids)})."
                                    if task.capability == "repair" and registered_ids
                                    else ""
                                )
                            ),
                        )
                    )
                    canonical_tools = [
                        CanonicalToolDefinition(
                            name=definition.name,
                            description=(
                                f"{definition.description} Registered ids: "
                                f"{', '.join(registered_ids)}."
                                if definition.name == "run_validation_command" and registered_ids
                                else definition.description
                            ),
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
                    diff_probe = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
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
                except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
                    # Typed kernel errors terminate the run; never downgrade to a
                    # task-level failure summary (P1.A / P1.E).
                    raise
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
                    except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
                        raise
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
            except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
                raise
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
                    lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
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
                expect_blocking = (
                    str(request.metadata.get("seed_review_expect_blocking") or "").lower() == "true"
                )
                seeded_paths = [
                    p.strip()
                    for p in str(request.metadata.get("seed_review_paths") or "").split(",")
                    if p.strip()
                ]
                expect_flag = str(request.metadata.get("seed_review_expect_blocking") or "").lower()
                if expect_blocking and seeded_paths:
                    evidence_path = seeded_paths[0]
                    task_findings.append(
                        Finding(
                            id=f"F-{task.id}-seed",
                            category="correctness",
                            severity="blocking",
                            summary=f"Seeded correctness defect in {evidence_path}",
                            explanation=(
                                "Deterministic mock reviewer detected the seeded broken "
                                f"implementation at {evidence_path}."
                            ),
                            evidence_refs=[patch_ref.model_copy(update={"scope": evidence_path})],
                            recommended_action=f"Repair the defect in {evidence_path}",
                            confidence=0.9,
                            produced_by=profile,
                        )
                    )
                elif expect_flag == "false" and seeded_paths:
                    evidence_path = seeded_paths[0]
                    task_findings.append(
                        Finding(
                            id=f"F-{task.id}-style",
                            category="maintainability",
                            severity="minor",
                            summary=f"Style-only note on {evidence_path}",
                            explanation="Cosmetic naming preference; not a correctness defect.",
                            evidence_refs=[patch_ref.model_copy(update={"scope": evidence_path})],
                            recommended_action="Optional rename; do not block merge",
                            confidence=0.8,
                            produced_by=profile,
                        )
                    )
                else:
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
                    task_findings.extend(
                        parse_raw_findings(
                            list(payload.get("findings") or []),
                            task_id=task.id,
                            produced_by=profile,
                            patch_ref=patch_ref,
                            review_patch=review_patch,
                            worktree_root=broker.worktree_root,
                            acceptance_criterion_ids=[ac.id for ac in task.acceptance_criteria],
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
            if composer_role in _QUALITY_GATE_ROLES:
                document_name = land_map.logical_name_for(
                    composer_role, default=_QUALITY_GATE_ROLES[composer_role]
                )
                document = self._compose_quality_document(
                    role=composer_role,
                    request=request,
                    dependency_outputs=dependency_outputs or [],
                    document_name=document_name,
                )
                art = artifacts.put_text(
                    document,
                    media_type="text/markdown",
                    logical_name=document_name,
                    created_by_task_id=task.id,
                )
                artifact_refs.append(art)
                summary = f"{composer_role} composed"
            elif request.workflow_type in _TECHNICAL_PLAN_WORKFLOW_TYPES:
                document_name = land_map.logical_name_for(
                    ROLE_ARCHITECTURE_DOCUMENT, default="ARCHITECTURE.md"
                )
                if isinstance(self._raw_gateway, MockGateway):
                    architecture_md = self._compose_architecture(
                        request.request_text, [], document_name=document_name
                    )
                else:
                    architecture_md, gen_usage = self._generate_architecture_document(
                        request=request,
                        task=task,
                        ctx_messages=ctx.messages,
                        run_id=run_id,
                        profile=profile,
                        dependency_outputs=dependency_outputs or [],
                        document_name=document_name,
                    )
                    model_usage = model_usage.merge(gen_usage)
                art = artifacts.put_text(
                    architecture_md,
                    media_type="text/markdown",
                    logical_name=document_name,
                    created_by_task_id=task.id,
                )
                artifact_refs.append(art)
                summary = "Architecture composed"
            elif request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES:
                evidence_name = land_map.logical_name_for(
                    ROLE_EVIDENCE_REPORT, default="EVIDENCE_REPORT.md"
                )
                evidence_report_md = self._compose_evidence_report(
                    request.request_text,
                    findings=task_findings,
                    dependency_outputs=dependency_outputs or [],
                    document_name=evidence_name,
                )
                art = artifacts.put_text(
                    evidence_report_md,
                    media_type="text/markdown",
                    logical_name=evidence_name,
                    created_by_task_id=task.id,
                )
                artifact_refs.append(art)
                summary = "Evidence report composed"
            else:
                if broker.worktree_root and base_commit:
                    # Composition is the deterministic diff of its inherited lineage.
                    patch = create_patch(broker.worktree_root, base_commit)
                    art = artifacts.put_text(
                        patch,
                        media_type="text/x-diff",
                        logical_name=land_map.logical_name_for(
                            ROLE_PROPOSED_PATCH, default="proposed.patch"
                        ),
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
                        lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
                else:
                    summary = "Nothing to compose"
        elif task.capability in {"architecture", "requirements"}:
            draft_text = ""
            if not isinstance(self._raw_gateway, MockGateway):
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
                            max_output_tokens=6000,
                            seed=(
                                int(request.metadata["benchmark_seed"])
                                if request.metadata.get("benchmark_seed") is not None
                                else None
                            ),
                        )
                    )
                    model_usage = model_usage.merge(resp.usage)
                    draft_text = (resp.text or "").strip()
                except BudgetExhaustedError:
                    raise
                except Exception:
                    draft_text = ""
            if draft_text:
                art = artifacts.put_text(
                    draft_text,
                    media_type="text/markdown",
                    logical_name=f"{task.id}-draft.md",
                    created_by_task_id=task.id,
                )
            else:
                art = artifacts.put_json(
                    {"objective": task.objective, "notes": "draft"},
                    logical_name=f"{task.id}.json",
                    created_by_task_id=task.id,
                )
            artifact_refs.append(art)
            summary = f"{task.capability} draft created"
        else:
            summary = f"Task {task.capability} completed (stub)"

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

    def _resolve_validation_command_ids(self, request: RunRequest) -> list[str]:
        """`RunRequest.validation_commands` is the source of truth (P1.C).

        Falls back to metadata `smoke_commands` (comma-separated) for bench
        runners that have not yet been migrated to the first-class field.
        """
        if request.validation_commands:
            return list(request.validation_commands)
        return [
            value.strip()
            for value in str(request.metadata.get("smoke_commands", "")).split(",")
            if value.strip()
        ]

    def _validate_outputs(
        self,
        *,
        request: RunRequest,
        patch_text: str,
        architecture_md: str,
        original_repo: Path | None,
        task: TaskSpec,
        findings: list[Finding] | None = None,
        ledger: BudgetLedger | None = None,
        evidence_report_md: str = "",
    ) -> list[ValidatorResult]:
        results: list[ValidatorResult] = []
        if request.workflow_type in _CODE_CHANGE_WORKFLOW_TYPES and patch_text and original_repo:
            results.append(validate_patch_applies(original_repo, patch_text))
            changed = self._changed_files_from_patch(patch_text)
            results.append(validate_path_scope(changed, task.allowed_path_patterns))
            results.append(validate_secrets(patch_text))
            command_ids = self._resolve_validation_command_ids(request)
            results.extend(
                validate_behavioral_commands(
                    repository=original_repo,
                    patch=patch_text,
                    command_ids=command_ids,
                    registered_commands=self.config.policies.registered_commands,
                    ledger=ledger,
                )
            )
        if request.workflow_type in _TECHNICAL_PLAN_WORKFLOW_TYPES and architecture_md:
            results.append(validate_architecture_document(architecture_md))
            must_cover = [
                item.strip()
                for item in str(request.metadata.get("must_cover") or "").split("|")
                if item.strip()
            ]
            if must_cover or not isinstance(self._raw_gateway, MockGateway):
                results.extend(
                    validate_architecture_request_specificity(
                        architecture_md,
                        must_cover=must_cover or None,
                        reject_boilerplate=not isinstance(self._raw_gateway, MockGateway),
                    )
                )
            results.append(validate_secrets(architecture_md))
        if request.workflow_type in _INVESTIGATION_WORKFLOW_TYPES and evidence_report_md:
            results.append(validate_investigation_document(evidence_report_md))
            results.append(validate_citations(evidence_report_md))
            results.append(validate_secrets(evidence_report_md))
        if task.capability == "independent_review" and findings is not None:
            # Demote unsupported blocking claims before scoring the evidence gate.
            preliminary = validate_review_findings(findings)
            if preliminary.status == "fail":
                bad_ids = set(preliminary.details.get("finding_ids") or [])
                for finding in findings:
                    if finding.id in bad_ids and finding.severity == "blocking":
                        finding.severity = "major"
                        finding.confidence = min(finding.confidence, 0.49)
            results.append(validate_review_findings(findings))
        return results

    def _changed_files_from_patch(self, patch: str) -> list[str]:
        files = []
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                files.append(line[6:])
        return files

    def _generate_architecture_document(
        self,
        *,
        request: RunRequest,
        task: TaskSpec,
        ctx_messages: list[dict[str, Any]],
        run_id: str,
        profile: str,
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "ARCHITECTURE.md",
    ) -> tuple[str, UsageMetrics]:
        """Ask the live model for a request-specific architecture document.

        `document_name` is the resolved deliverable name, so a run that asked for
        `integration_testing_architecture.md` gets a document scoped to that
        subject rather than a whole-system template.
        """
        must_cover = [
            item.strip()
            for item in str(request.metadata.get("must_cover") or "").split("|")
            if item.strip()
        ]
        section_list = ", ".join(ARCHITECTURE_REQUIRED_SECTIONS)
        system = (
            f"You are the architecture composer. Write a complete {document_name} "
            "markdown document for the user request. Use these section headings "
            f"(as markdown ## headings): {section_list}. "
            "Every section must contain request-specific detail — never generic "
            "boilerplate such as 'MVP scope as requested' or 'Deliver the requested "
            "capabilities'. Include at least one mermaid data-flow diagram when useful. "
            "Return markdown only."
        )
        payload = {
            "request": request.request_text,
            "deliverable_name": document_name,
            "task_objective": task.objective,
            "must_cover_topics": must_cover,
            "reference_hints": request.metadata.get("reference_hints") or "",
            "dependency_drafts": dependency_outputs[:4],
            "prior_context_messages": ctx_messages[-4:],
        }
        usage = UsageMetrics()
        try:
            resp = self.gateway.complete(
                ModelRequest(
                    request_id=f"arch-{uuid.uuid4().hex[:8]}",
                    run_id=run_id,
                    task_id=task.id,
                    session_id=f"pf:{run_id}:{profile}:{task.id}",
                    model_profile=profile,
                    messages=[
                        CanonicalMessage(role="system", content=system),
                        CanonicalMessage(
                            role="user",
                            content=json.dumps(payload, indent=2, default=str),
                        ),
                    ],
                    max_output_tokens=8000,
                    temperature=0.2,
                    seed=(
                        int(request.metadata["benchmark_seed"])
                        if request.metadata.get("benchmark_seed") is not None
                        else None
                    ),
                    max_cost_usd=float(request.budget.max_cost_usd),
                )
            )
            usage = resp.usage
            text = (resp.text or "").strip()
            if text:
                if not text.lstrip().startswith("#"):
                    text = f"# {document_name}\n\n{text}"
                return text, usage
        except BudgetExhaustedError:
            raise
        except Exception:
            pass
        # Fail closed toward an explicit thin draft rather than silent template success.
        fallback = (
            f"# {document_name}\n\n## Objective\n{request.request_text.strip()}\n\n"
            "## Scope\nGeneration failed; document incomplete.\n\n"
            "## Assumptions\n- None captured.\n\n"
            "## Functional requirements\n- None captured.\n\n"
            "## Nonfunctional requirements\n- None captured.\n\n"
            "## Components\n- None captured.\n\n"
            "## Data flows\nNone captured.\n\n"
            "## Security\nNone captured.\n\n"
            "## Testing\nNone captured.\n\n"
            "## Trade-offs\nNone captured.\n\n"
            "## Open questions\n- Architecture generation failed.\n\n"
            "## Acceptance criteria\n- Regenerate architecture document.\n"
        )
        return fallback, usage

    def _compose_architecture(
        self,
        request_text: str,
        findings: list[Finding],
        *,
        document_name: str = "ARCHITECTURE.md",
    ) -> str:
        sections = [
            f"# {document_name}",
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

    def _compose_evidence_report(
        self,
        request_text: str,
        *,
        findings: list[Finding],
        dependency_outputs: list[dict[str, Any]],
        document_name: str = "EVIDENCE_REPORT.md",
    ) -> str:
        """Deterministic evidence report with cited paths and assumptions (P3.D)."""
        cited_paths: list[str] = []
        for prior in dependency_outputs:
            for excerpt in prior.get("artifact_excerpts") or []:
                if excerpt.get("logical_name") != "repository-analysis.json":
                    continue
                try:
                    payload = json.loads(excerpt.get("content") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("files", "entry_points", "tests", "configuration"):
                    for path in payload.get(key) or []:
                        path_s = str(path).strip()
                        if path_s and path_s not in cited_paths:
                            cited_paths.append(path_s)
                for item in payload.get("relevant_excerpts") or []:
                    if isinstance(item, dict) and item.get("path"):
                        path_s = str(item["path"]).strip()
                        if path_s and path_s not in cited_paths:
                            cited_paths.append(path_s)
        if not cited_paths:
            cited_paths = ["README.md"]
        cited_paths = cited_paths[:20]
        finding_lines = [f"- {f.summary} (see `{cited_paths[0]}`)" for f in findings] or [
            f"- Request focuses on: {request_text.strip()[:240] or 'repository structure'}",
            f"- Observed entry points and modules under `{cited_paths[0]}`",
        ]
        assumption_lines = [
            "- Analysis is read-only; no repository mutations were performed.",
            "- Path citations come from repository listing and targeted excerpts.",
            "- Scope is limited to files visible in the snapshotted worktree.",
        ]
        sections = [
            f"# {document_name}",
            "",
            "## Summary",
            request_text.strip() or "Repository investigation",
            "",
            "## Findings",
            *finding_lines,
            "",
            "## Cited paths",
            *[f"- `{path}`" for path in cited_paths],
            "",
            "## Assumptions",
            *assumption_lines,
            "",
        ]
        return "\n".join(sections) + "\n"

    @staticmethod
    def _inherited_findings(dependency_outputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Findings produced upstream, deduplicated by id in dependency order."""
        seen: set[str] = set()
        collected: list[dict[str, Any]] = []
        for prior in dependency_outputs:
            for finding in prior.get("findings") or []:
                if not isinstance(finding, dict):
                    continue
                finding_id = str(finding.get("id") or "")
                if finding_id and finding_id in seen:
                    continue
                if finding_id:
                    seen.add(finding_id)
                collected.append(finding)
        return collected

    @staticmethod
    def _scoped_paths(dependency_outputs: list[dict[str, Any]]) -> list[str]:
        """Repository paths an upstream scoping task actually listed.

        Read from the `repository-analysis.json` excerpt so a plan cites files a
        reviewer can open, ordered tests-first because those carry the coverage
        signal a quality gate reports on.
        """
        paths: list[str] = []
        for prior in dependency_outputs:
            for excerpt in prior.get("artifact_excerpts") or []:
                if excerpt.get("logical_name") != "repository-analysis.json":
                    continue
                try:
                    payload = json.loads(excerpt.get("content") or "{}")
                except json.JSONDecodeError:
                    continue
                for key in ("tests", "entry_points", "configuration", "files"):
                    for path in payload.get(key) or []:
                        path_s = str(path).strip()
                        if path_s and path_s not in paths:
                            paths.append(path_s)
                # Excerpted paths are the reliable signal: the listing is empty
                # whenever a read-only task's glob matched nothing.
                for item in payload.get("relevant_excerpts") or []:
                    if isinstance(item, dict) and item.get("path"):
                        path_s = str(item["path"]).strip()
                        if path_s and path_s not in paths:
                            paths.append(path_s)
        return paths

    @staticmethod
    def _evidence_paths(findings: list[dict[str, Any]]) -> list[str]:
        """Path-like evidence scopes cited by findings, in first-seen order."""
        paths: list[str] = []
        for finding in findings:
            for ref in finding.get("evidence_refs") or []:
                if not isinstance(ref, dict):
                    continue
                scope = str(ref.get("scope") or "").strip()
                if not scope or scope in {"patch", "review_input"}:
                    continue
                if scope not in paths:
                    paths.append(scope)
        return paths

    def _compose_quality_document(
        self,
        *,
        role: str,
        request: RunRequest,
        dependency_outputs: list[dict[str, Any]],
        document_name: str,
    ) -> str:
        """Deterministic quality-gate deliverable for one land-map role (P4.E).

        Each document carries the sections its pack declares and cites the
        evidence paths that upstream tasks actually recorded, so a findings report
        never asserts a defect without a path a reviewer can open.
        """
        inherited = self._inherited_findings(dependency_outputs)
        scoped_paths = self._scoped_paths(dependency_outputs)
        evidence_paths = self._evidence_paths(inherited) or scoped_paths[:5] or ["README.md"]
        objective = request.request_text.strip() or "Repository quality review"
        commands = self._resolve_validation_command_ids(request)

        if role == ROLE_TEST_PLAN:
            ranked = (scoped_paths or evidence_paths)[:10]
            risk_lines = [f"- `{path}` — exercised by the checks below" for path in ranked]
            case_lines = [
                f"- Verify behavior covered by `{path}` against its acceptance criteria"
                for path in ranked
            ]
            if commands:
                case_lines.extend(
                    f"- Registered validation command `{command}`" for command in commands
                )
            sections = [
                f"# {document_name}",
                "",
                "## Summary",
                objective,
                "",
                "## Risk areas",
                *risk_lines,
                "",
                "## Test cases",
                *case_lines,
                "",
                "## Coverage gaps",
                *(
                    ["- No registered validation commands were configured for this run."]
                    if not commands
                    else ["- Paths outside the reviewed scope remain unverified."]
                ),
                "",
            ]
            return "\n".join(sections) + "\n"

        if role == ROLE_SECURITY_EVIDENCE:
            security = [
                finding
                for finding in inherited
                if str(finding.get("category") or "").lower() == "security"
            ]
            finding_lines = [
                f"- {finding.get('summary') or 'Security observation'} "
                f"({finding.get('severity') or 'minor'})"
                for finding in security
            ] or ["- No security-specific defects were identified in the reviewed scope."]
            sections = [
                f"# {document_name}",
                "",
                "## Summary",
                objective,
                "",
                "## Checks performed",
                "- Secret patterns scanned across composed deliverables.",
                "- Repository read-only inspection of the paths cited below.",
                "",
                "## Findings",
                *finding_lines,
                "",
                "## Evidence",
                *[f"- `{path}`" for path in evidence_paths],
                "",
            ]
            return "\n".join(sections) + "\n"

        blocking = [finding for finding in inherited if str(finding.get("severity")) == "blocking"]
        finding_lines = [
            f"- [{finding.get('severity') or 'minor'}] "
            f"{finding.get('summary') or 'Finding'} — see "
            f"`{(self._evidence_paths([finding]) or evidence_paths)[0]}`"
            for finding in inherited
        ] or ["- No defects were identified in the reviewed scope."]
        action_lines = [
            f"- {finding.get('recommended_action') or 'Review and triage'}"
            for finding in inherited
            if finding.get("recommended_action")
        ] or ["- No action required from this gate."]
        sections = [
            f"# {document_name}",
            "",
            "## Summary",
            objective,
            "",
            f"Blocking findings: {len(blocking)}. Total findings: {len(inherited)}.",
            "",
            "## Findings",
            *finding_lines,
            "",
            "## Evidence",
            *[f"- `{path}`" for path in evidence_paths],
            "",
            "## Recommended actions",
            *action_lines,
            "",
        ]
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
