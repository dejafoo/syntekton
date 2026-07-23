"""Subject runners for orchestration, single-agent, isolation, and frontier."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from product_factory.config.loader import AppConfig
from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import AcceptanceCriterion, TaskSpec
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.subjects import SubjectArtifact, SubjectConfig
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.coordinator import RunCoordinator, extract_unified_diff


def _clone_repo(src: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    if (src / ".git").exists():
        result = subprocess.run(
            ["git", "clone", "--local", str(src), str(dest)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return dest
    # Fixtures are stored as plain trees; materialize a disposable git repo.
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".git"))
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=fixture@example.com",
            "-c",
            "user.name=Fixture",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=dest,
        check=True,
        capture_output=True,
    )
    return dest


def _resolve_repo(case: EvalCase, app_root: Path, work_dir: Path) -> Path | None:
    if not case.repository:
        return None
    src = (app_root / case.repository).resolve()
    if not src.exists():
        return None
    return _clone_repo(src, work_dir / "repo")


def _augment_request_text(case: EvalCase) -> str:
    parts = [case.request.strip()]
    if case.acceptance_criteria:
        parts.append(
            "Acceptance criteria:\n"
            + "\n".join(f"- {item}" for item in case.acceptance_criteria)
        )
    if case.must_cover:
        parts.append(
            "Must-cover topics (architecture must address each explicitly):\n"
            + "\n".join(f"- {item}" for item in case.must_cover)
        )
    if case.reference_hints:
        parts.append(f"Reference hints:\n{case.reference_hints.strip()}")
    if case.expected_files:
        parts.append(
            "Required deliverable paths (create or modify exactly these paths):\n"
            + "\n".join(f"- {path}" for path in case.expected_files)
        )
    return "\n\n".join(parts)


class FullOrchestrationRunner:
    subject_id = "full_orchestration"

    def __init__(self, app_config: AppConfig, *, use_deterministic_planner: bool = False) -> None:
        self.app_config = app_config
        self.use_deterministic_planner = use_deterministic_planner

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        work_dir.mkdir(parents=True, exist_ok=True)
        repo = _resolve_repo(case, self.app_config.root, work_dir)
        use_det = self.use_deterministic_planner or isinstance(gateway, MockGateway)
        coord = RunCoordinator(
            config=self.app_config,
            gateway=gateway,
            data_dir=work_dir / ".product-factory",
            use_deterministic_planner=use_det,
        )
        req = RunRequest(
            request_id=f"bench-{uuid.uuid4().hex[:8]}",
            workflow_type=case.workflow_type,
            request_text=_augment_request_text(case),
            repository_path=repo,
            budget=RunBudget(max_cost_usd=case.budgets.max_cost_usd),
            metadata={
                "eval_case": case.id,
                "subject": self.subject_id,
                "benchmark_seed": str(int(case.metadata.get("benchmark_seed", 0))),
                "smoke_commands": ",".join(case.smoke_commands),
                "disable_review": str(bool(case.metadata.get("disable_review", False))).lower(),
                "force_review": str(bool(case.metadata.get("force_review", False))).lower(),
                "disable_analysis": str(
                    bool(case.metadata.get("disable_analysis", False))
                ).lower(),
                "context_mode": str(case.metadata.get("context_mode", "targeted")),
                # Live planner schemas remain unreliable across providers; default to
                # the deterministic plan unless an ablation explicitly requests live.
                "planner_mode": str(case.metadata.get("planner_mode", "fixed")),
                "expected_files": ",".join(case.expected_files),
                "disable_validation_repair": str(
                    bool(case.metadata.get("disable_validation_repair", False))
                ).lower(),
                "force_seeded_impl": str(
                    bool(case.metadata.get("force_seeded_impl", False))
                ).lower(),
                "seed_repair_defect": str(
                    case.metadata.get("seed_repair_defect") or ""
                ),
                "implementation_model_profile": str(
                    case.metadata.get("implementation_model_profile") or ""
                ),
                "must_cover": "|".join(case.must_cover),
                "reference_hints": case.reference_hints or "",
            },
        )
        try:
            manifest = coord.run(req)
        except Exception as exc:
            return SubjectArtifact(
                subject_id="full_orchestration",
                case_id=case.id,
                status="failed",
                error=str(exc),
                model_profile=config.model_profile,
            )
        run_dir = work_dir / ".product-factory" / "runs" / manifest.run_id
        artifact_text = ""
        artifact_kind: str = "other"
        changed: list[str] = []
        selected_path: Path | None = None
        out = run_dir / "output"
        if case.workflow_type == "architecture":
            path = out / "ARCHITECTURE.md"
            if path.exists():
                artifact_text = path.read_text(encoding="utf-8")
                artifact_kind = "architecture"
        else:
            for name in ("proposed.patch", "implementation.patch"):
                path = out / name
                if path.exists() and path.stat().st_size > 0:
                    candidate = path.read_text(encoding="utf-8")
                    if not candidate.strip():
                        continue
                    artifact_text = candidate
                    artifact_kind = "patch"
                    selected_path = path
                    break
            for line in artifact_text.splitlines():
                if line.startswith("+++ b/"):
                    changed.append(line[6:])
        return SubjectArtifact(
            subject_id="full_orchestration",
            case_id=case.id,
            status=manifest.final_status,
            artifact_text=artifact_text,
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            changed_files=changed,
            run_id=manifest.run_id,
            model_profile=config.model_profile,
            usage=manifest.usage,
            artifact_path=selected_path if case.workflow_type == "code_change" else None,
            metadata={
                "approval": manifest.final_status,
                "selected_artifact": str(selected_path) if selected_path else None,
                "selection_reason": (
                    "first_non_empty_valid_candidate" if selected_path else "no_non_empty_patch"
                ),
                "seed": int(case.metadata.get("benchmark_seed", 0)),
                "deterministic_fallback_used": isinstance(gateway, MockGateway),
                "live_fallback_used": False,
            },
            error=(
                (
                    "; ".join(
                        note
                        for note in manifest.notes
                        if note and not note.startswith("no_progress_count=")
                    )
                    or None
                )
                if manifest.final_status == "failed"
                else None
            ),
        )


class SingleAgentBaselineRunner:
    """One model, one pass — no multi-agent orchestration."""

    subject_id = "single_agent_baseline"

    def __init__(self, app_config: AppConfig) -> None:
        self.app_config = app_config

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        work_dir.mkdir(parents=True, exist_ok=True)
        repo = _resolve_repo(case, self.app_config.root, work_dir)
        system = (
            "You are a single coding/architecture agent without multi-agent orchestration. "
            "Produce the final deliverable only. For code_change return a unified diff patch. "
            "For architecture return a complete ARCHITECTURE.md markdown document."
        )
        user = json.dumps(
            {
                "workflow_type": case.workflow_type,
                "request": _augment_request_text(case),
                "acceptance_criteria": case.acceptance_criteria,
                "expected_files": case.expected_files,
                "repository_files": _list_repo_files(repo) if repo else [],
            },
            indent=2,
        )
        req = ModelRequest(
            request_id=f"single-{uuid.uuid4().hex[:8]}",
            run_id=f"bench-{case.id}",
            task_id="single",
            session_id=f"pf:bench:single:{case.id}",
            model_profile=config.model_profile,
            messages=[
                CanonicalMessage(role="system", content=system),
                CanonicalMessage(role="user", content=user),
            ],
            max_output_tokens=8000,
            temperature=0.1,
            seed=int(case.metadata.get("benchmark_seed", 0)),
            max_cost_usd=float(case.budgets.max_cost_usd),
        )
        try:
            resp = gateway.complete(req)
        except Exception as exc:
            return SubjectArtifact(
                subject_id=self.subject_id,  # type: ignore[arg-type]
                case_id=case.id,
                status="failed",
                error=str(exc),
                model_profile=config.model_profile,
            )
        text = resp.text or ""
        # Mock gateway: synthesize a minimal artifact
        if isinstance(gateway, MockGateway) and (
            not text or text == "mock response" or resp.structured_data is not None
        ):
            text = _mock_artifact_for_case(case, repo)
        kind = "architecture" if case.workflow_type == "architecture" else "patch"
        if kind == "patch":
            text = extract_unified_diff(text)
        out = work_dir / ("ARCHITECTURE.md" if kind == "architecture" else "proposed.patch")
        out.write_text(text, encoding="utf-8")
        changed = []
        if kind == "patch":
            for line in text.splitlines():
                if line.startswith("+++ b/"):
                    changed.append(line[6:])
        return SubjectArtifact(
            subject_id=self.subject_id,  # type: ignore[arg-type]
            case_id=case.id,
            status="completed",
            artifact_text=text,
            artifact_kind=kind,  # type: ignore[arg-type]
            artifact_path=out,
            changed_files=changed,
            model_profile=config.model_profile,
            resolved_model_id=resp.resolved_model_id,
            provider=resp.provider,
            usage=resp.usage,
            prompt_package_hash=resp.response_hash,
        )


class OrchestrationAblationRunner(FullOrchestrationRunner):
    """Named orchestration shape with controlled metadata switches."""

    def __init__(
        self,
        app_config: AppConfig,
        *,
        subject_id: str,
        metadata: dict[str, object],
        use_deterministic_planner: bool = False,
    ) -> None:
        super().__init__(
            app_config, use_deterministic_planner=use_deterministic_planner
        )
        self.subject_id = subject_id
        self.metadata = metadata

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        configured = case.model_copy(
            update={"metadata": {**case.metadata, **self.metadata}}
        )
        artifact = super().run(
            configured, config=config, gateway=gateway, work_dir=work_dir
        )
        return artifact.model_copy(update={"subject_id": self.subject_id})


class AgentIsolationRunner:
    """Run one capability in isolation with a fixed TaskSpec.

    For code-change implementation isolation, reuse the orchestration
    implementation agent loop (tools + worktree) without analysis, review, or
    validation/repair — otherwise the subject is an unfair one-shot chat baseline.
    """

    subject_id = "agent_isolation"

    def __init__(
        self,
        app_config: AppConfig,
        *,
        use_deterministic_planner: bool = False,
    ) -> None:
        self.app_config = app_config
        self.use_deterministic_planner = use_deterministic_planner
        self._impl_runner = OrchestrationAblationRunner(
            app_config,
            subject_id=self.subject_id,
            metadata={
                "disable_review": True,
                "disable_analysis": True,
                "disable_validation_repair": True,
                "planner_mode": "fixed",
            },
            use_deterministic_planner=use_deterministic_planner,
        )

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        capability = config.isolation_capability or (
            case.isolation_targets[0] if case.isolation_targets else "implementation"
        )
        if case.workflow_type == "code_change" and capability == "implementation":
            artifact = self._impl_runner.run(
                case, config=config, gateway=gateway, work_dir=work_dir
            )
            return artifact.model_copy(
                update={
                    "subject_id": self.subject_id,
                    "metadata": {
                        **artifact.metadata,
                        "capability": capability,
                        "isolation_mode": "agent_loop",
                    },
                }
            )

        work_dir.mkdir(parents=True, exist_ok=True)
        repo = _resolve_repo(case, self.app_config.root, work_dir)
        task = TaskSpec(
            id="ISO-001",
            title=f"Isolated {capability}",
            capability=capability,  # type: ignore[arg-type]
            objective=case.request,
            expected_output_schema=f"{capability}.v1",
            acceptance_criteria=[
                AcceptanceCriterion(
                    id="iso-ac1",
                    description=ac,
                    verification="artifact_check",
                )
                for ac in (case.acceptance_criteria or ["Produce usable artifact"])
            ],
        )
        system = (
            f"You are an isolated {capability} agent. "
            "Do not assume other agents will finish the work. Produce the final artifact only."
        )
        user = json.dumps(
            {
                "task": task.model_dump(mode="json"),
                "request": _augment_request_text(case),
                "repository_files": _list_repo_files(repo) if repo else [],
                "dependency_context": "none (isolation)",
            },
            indent=2,
            default=str,
        )
        # Prefer coding_worker for implementation isolation
        profile = config.model_profile
        if capability == "implementation" and profile == "supervisor":
            profile = "coding_worker"
        req = ModelRequest(
            request_id=f"iso-{uuid.uuid4().hex[:8]}",
            run_id=f"bench-{case.id}",
            task_id=task.id,
            session_id=f"pf:bench:iso:{case.id}:{capability}",
            model_profile=profile,
            messages=[
                CanonicalMessage(role="system", content=system),
                CanonicalMessage(role="user", content=user),
            ],
            max_output_tokens=8000,
            temperature=0.1,
            seed=int(case.metadata.get("benchmark_seed", 0)),
            max_cost_usd=float(case.budgets.max_cost_usd),
        )
        try:
            resp = gateway.complete(req)
        except Exception as exc:
            return SubjectArtifact(
                subject_id="agent_isolation",
                case_id=case.id,
                status="failed",
                error=str(exc),
                model_profile=profile,
                metadata={"capability": capability},
            )
        text = resp.text or ""
        if isinstance(gateway, MockGateway) and (
            not text or text == "mock response" or resp.structured_data is not None
        ):
            text = _mock_artifact_for_case(case, repo)
        kind = "architecture" if case.workflow_type == "architecture" else "patch"
        if kind == "patch":
            text = extract_unified_diff(text)
        return SubjectArtifact(
            subject_id="agent_isolation",
            case_id=case.id,
            status="completed",
            artifact_text=text,
            artifact_kind=kind,  # type: ignore[arg-type]
            changed_files=[line[6:] for line in text.splitlines() if line.startswith("+++ b/")],
            model_profile=profile,
            resolved_model_id=resp.resolved_model_id,
            provider=resp.provider,
            usage=resp.usage,
            metadata={"capability": capability, "isolation_mode": "one_shot"},
        )


class FrontierReferenceRunner(SingleAgentBaselineRunner):
    subject_id = "frontier_reference"

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        cfg = config.model_copy(update={"model_profile": "frontier_oracle"})
        art = super().run(case, config=cfg, gateway=gateway, work_dir=work_dir)
        return art.model_copy(update={"subject_id": "frontier_reference"})


class IsolationAblationRunner(AgentIsolationRunner):
    """Lone implementation agent with tools; no analysis/review/repair."""

    subject_id = "implementation_isolation"

    def __init__(
        self,
        app_config: AppConfig,
        *,
        use_deterministic_planner: bool = False,
    ) -> None:
        super().__init__(
            app_config, use_deterministic_planner=use_deterministic_planner
        )
        self._impl_runner = OrchestrationAblationRunner(
            app_config,
            subject_id=self.subject_id,
            metadata={
                "disable_review": True,
                "disable_analysis": True,
                "disable_validation_repair": True,
                "planner_mode": "fixed",
            },
            use_deterministic_planner=use_deterministic_planner,
        )

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        artifact = super().run(case, config=config, gateway=gateway, work_dir=work_dir)
        return artifact.model_copy(update={"subject_id": self.subject_id})


class SeededRepairRunner(OrchestrationAblationRunner):
    """Plant a known-broken candidate, then measure repair recovery."""

    subject_id = "seeded_repair"

    def __init__(
        self,
        app_config: AppConfig,
        *,
        use_deterministic_planner: bool = False,
    ) -> None:
        super().__init__(
            app_config,
            subject_id=self.subject_id,
            metadata={
                "disable_review": True,
                "disable_analysis": True,
                "force_seeded_impl": True,
                "planner_mode": "fixed",
            },
            use_deterministic_planner=use_deterministic_planner,
        )

    def run(
        self,
        case: EvalCase,
        *,
        config: SubjectConfig,
        gateway: ModelGateway,
        work_dir: Path,
    ) -> SubjectArtifact:
        from product_factory.evaluation.defects import resolve_defect_kind

        kind = resolve_defect_kind(
            case.id,
            explicit=str(case.metadata.get("seed_repair_defect") or "") or None,
        )
        configured = case.model_copy(
            update={
                "metadata": {
                    **case.metadata,
                    **self.metadata,
                    "seed_repair_defect": kind,
                    "force_seeded_impl": True,
                }
            }
        )
        # Ensure smoke runs so seeded defects are observed.
        if configured.workflow_type == "code_change" and not configured.smoke_commands:
            configured = configured.model_copy(update={"smoke_commands": ["python_tests"]})
        artifact = FullOrchestrationRunner.run(
            self, configured, config=config, gateway=gateway, work_dir=work_dir
        )
        repair_lineage = False
        if artifact.run_id:
            run_out = (
                work_dir
                / ".product-factory"
                / "runs"
                / artifact.run_id
                / "output"
            )
            repair_lineage = any(run_out.glob("R-*-lineage.json"))
        return artifact.model_copy(
            update={
                "subject_id": self.subject_id,
                "metadata": {
                    **artifact.metadata,
                    "seed_repair_defect": kind,
                    "repair_triggered": repair_lineage,
                    "force_seeded_impl": True,
                },
            }
        )


def _list_repo_files(repo: Path | None, limit: int = 40) -> list[str]:
    if repo is None:
        return []
    files = []
    for p in sorted(repo.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            files.append(str(p.relative_to(repo)))
            if len(files) >= limit:
                break
    return files


def _mock_artifact_for_case(case: EvalCase, repo: Path | None) -> str:
    if case.workflow_type == "architecture":
        must = case.must_cover or ["request-specific design"]
        cover_lines = "\n".join(f"- Explicitly covers: {topic}." for topic in must)
        detail = (
            f"{case.request.strip()}\n\n"
            "This design elaborates concrete components, trust boundaries, failure "
            "modes, and verification for the stated domain rather than a generic "
            "service shell. "
            + " ".join(must)
        )
        sections = [
            "# ARCHITECTURE.md",
            "",
            "## Objective",
            detail,
            "",
            "## Scope",
            "MVP includes the must-cover flows below and excludes unrelated platform work.",
            cover_lines,
            "",
            "## Assumptions",
            "- Operators can provision one primary region for the MVP.",
            "- Secrets and credentials are injected from an external secret store.",
            "",
            "## Functional requirements",
            cover_lines,
            "- Operators can observe and roll back failed workflows.",
            "",
            "## Nonfunctional requirements",
            "- Isolation, auditability, and recoverable failure handling are mandatory.",
            "",
            "## Components",
            "- Edge API / UI, domain services, persistence, and async workers as needed.",
            "",
            "## Data flows",
            "```mermaid",
            "flowchart LR",
            "  User --> Edge --> Domain --> Store",
            "```",
            "",
            "## Security",
            "- Authn/authz at the edge; least-privilege data access; audited admin paths.",
            "",
            "## Testing",
            "- Unit, contract, isolation, and abuse/regression tests for must-cover risks.",
            "",
            "## Trade-offs",
            "- Prefer a simpler modular monolith until traffic or tenancy forces split.",
            "",
            "## Open questions",
            "- Exact SLOs and compliance evidence packs.",
            "",
            "## Acceptance criteria",
            cover_lines,
            "",
        ]
        return "\n".join(sections)
    # Minimal patch for coding cases
    target = "src/app/util.py"
    if case.expected_files:
        target = case.expected_files[0]
    return (
        f"diff --git a/{target} b/{target}\n"
        f"new file mode 100644\n"
        f"--- /dev/null\n"
        f"+++ b/{target}\n"
        f"@@ -0,0 +1,3 @@\n"
        f'+"""Generated by single-agent/mock baseline."""\n'
        f"+VALUE = True\n"
        f"+\n"
    )


def default_subject_configs() -> dict[str, SubjectConfig]:
    return {
        "full_orchestration": SubjectConfig(
            subject_id="full_orchestration",
            model_profile="supervisor",
            description="Multi-agent MVP orchestration",
        ),
        "single_agent_baseline": SubjectConfig(
            subject_id="single_agent_baseline",
            model_profile="supervisor",
            description="Single strong agent without orchestration",
        ),
        "agent_isolation": SubjectConfig(
            subject_id="agent_isolation",
            model_profile="coding_worker",
            isolation_capability="implementation",
            description="Single capability in isolation",
        ),
        "implementation_isolation": SubjectConfig(
            subject_id="implementation_isolation",
            model_profile="coding_worker",
            isolation_capability="implementation",
            description="Implementation worker in isolation",
        ),
        "orchestration_validation_repair": SubjectConfig(
            subject_id="orchestration_validation_repair",
            model_profile="supervisor",
            description="Implementation plus validation and repair",
        ),
        "full_orchestration_no_review": SubjectConfig(
            subject_id="full_orchestration_no_review",
            model_profile="supervisor",
            description="Full orchestration with review disabled",
        ),
        "full_orchestration_with_review": SubjectConfig(
            subject_id="full_orchestration_with_review",
            model_profile="supervisor",
            description="Full orchestration with review forced",
        ),
        "orchestration_file_list_context": SubjectConfig(
            subject_id="orchestration_file_list_context",
            model_profile="supervisor",
            description="File-list-only context ablation",
        ),
        "orchestration_targeted_context": SubjectConfig(
            subject_id="orchestration_targeted_context",
            model_profile="supervisor",
            description="Targeted excerpt context ablation",
        ),
        "orchestration_fixed_planner": SubjectConfig(
            subject_id="orchestration_fixed_planner",
            model_profile="supervisor",
            description="Fixed deterministic planner ablation",
        ),
        "orchestration_live_planner": SubjectConfig(
            subject_id="orchestration_live_planner",
            model_profile="supervisor",
            description="Live planner ablation",
        ),
        "orchestration_complexity_planner": SubjectConfig(
            subject_id="orchestration_complexity_planner",
            model_profile="supervisor",
            description="Complexity-sensitive planner ablation",
        ),
        "orchestration_strong_worker": SubjectConfig(
            subject_id="orchestration_strong_worker",
            model_profile="local_target_reviewer",
            description="Fixed planner with stronger implementation worker",
        ),
        "frontier_reference": SubjectConfig(
            subject_id="frontier_reference",
            model_profile="frontier_oracle",
            description="Frontier reference subject",
        ),
        "seeded_repair": SubjectConfig(
            subject_id="seeded_repair",
            model_profile="supervisor",
            description="Seeded broken candidate then stateful repair",
        ),
    }
