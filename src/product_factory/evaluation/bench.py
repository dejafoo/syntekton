"""Benchmark orchestrator — run subjects, judge, compare, export lessons."""

from __future__ import annotations

import random
import uuid
from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import AppConfig
from product_factory.evaluation.adapters.base import CaseLoader, LocalYamlCaseLoader
from product_factory.evaluation.compare import ComparisonReport, build_comparison
from product_factory.evaluation.deterministic import (
    deterministic_summary,
    merge_scores,
    run_deterministic_checks,
)
from product_factory.evaluation.judge import Judge, LLMJudge, MockJudge
from product_factory.evaluation.lessons import extract_lessons, write_lesson_candidates
from product_factory.evaluation.runners import (
    AgentIsolationRunner,
    FrontierReferenceRunner,
    FullOrchestrationRunner,
    IsolationAblationRunner,
    OrchestrationAblationRunner,
    SingleAgentBaselineRunner,
    default_subject_configs,
)
from product_factory.evaluation.store import EvalStore
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.persistence.database import Database


def _instrument_gateway(gateway: ModelGateway, db: Database, content_dir: Path) -> ModelGateway:
    if isinstance(gateway, (MockGateway, InstrumentedModelGateway)):
        return gateway
    recorder = TelemetryRecorder(db, content_dir=content_dir)
    return InstrumentedModelGateway(gateway, recorder=recorder, db=db)


class BenchmarkRunner:
    def __init__(
        self,
        *,
        app_config: AppConfig,
        gateway: ModelGateway,
        judge: Judge | None = None,
        data_dir: Path | None = None,
        usable_threshold: int = 3,
        use_deterministic_planner: bool | None = None,
    ) -> None:
        self.app_config = app_config
        self.pf_root = data_dir or (app_config.root / ".product-factory")
        self.pf_root.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.pf_root / "data" / "product_factory.sqlite")
        self.store = EvalStore(self.db)
        self.gateway = _instrument_gateway(
            gateway, self.db, self.pf_root / "content" / "bench"
        )
        if judge is not None:
            self.judge = judge
            if isinstance(judge, LLMJudge) and not isinstance(judge.gateway, InstrumentedModelGateway):
                judge.gateway = _instrument_gateway(
                    judge.gateway, self.db, self.pf_root / "content" / "bench"
                )
        elif isinstance(gateway, MockGateway):
            self.judge = MockJudge()
        else:
            self.judge = LLMJudge(self.gateway, model_profile="grok_judge")
        self.usable_threshold = usable_threshold
        if use_deterministic_planner is None:
            use_deterministic_planner = isinstance(gateway, MockGateway)
        self.use_deterministic_planner = use_deterministic_planner
        self._runners = {
            "full_orchestration": FullOrchestrationRunner(
                app_config, use_deterministic_planner=use_deterministic_planner
            ),
            "single_agent_baseline": SingleAgentBaselineRunner(app_config),
            "agent_isolation": AgentIsolationRunner(app_config),
            "implementation_isolation": IsolationAblationRunner(app_config),
            "orchestration_validation_repair": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_validation_repair",
                metadata={
                    "disable_review": True,
                    "disable_analysis": True,
                    "planner_mode": "fixed",
                },
                use_deterministic_planner=use_deterministic_planner,
            ),
            "full_orchestration_no_review": OrchestrationAblationRunner(
                app_config,
                subject_id="full_orchestration_no_review",
                metadata={"disable_review": True},
                use_deterministic_planner=use_deterministic_planner,
            ),
            "full_orchestration_with_review": OrchestrationAblationRunner(
                app_config,
                subject_id="full_orchestration_with_review",
                metadata={"force_review": True},
                use_deterministic_planner=use_deterministic_planner,
            ),
            "orchestration_file_list_context": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_file_list_context",
                metadata={
                    "disable_review": True,
                    "context_mode": "file_list_only",
                    "planner_mode": "fixed",
                },
                use_deterministic_planner=True,
            ),
            "orchestration_targeted_context": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_targeted_context",
                metadata={
                    "disable_review": True,
                    "context_mode": "targeted",
                    "planner_mode": "fixed",
                },
                use_deterministic_planner=True,
            ),
            "orchestration_fixed_planner": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_fixed_planner",
                metadata={"disable_review": True, "planner_mode": "fixed"},
                use_deterministic_planner=True,
            ),
            "orchestration_live_planner": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_live_planner",
                metadata={"disable_review": True, "planner_mode": "live"},
                use_deterministic_planner=False,
            ),
            "orchestration_complexity_planner": OrchestrationAblationRunner(
                app_config,
                subject_id="orchestration_complexity_planner",
                metadata={
                    "disable_review": True,
                    "planner_mode": "complexity_sensitive",
                },
                use_deterministic_planner=True,
            ),
            "frontier_reference": FrontierReferenceRunner(app_config),
        }

    def run(
        self,
        *,
        cases_dir: Path,
        subjects: list[str],
        limit: int = 10,
        suite: str = "local",
        oracle_budget_usd: Decimal = Decimal("5.00"),
        case_loader: CaseLoader | None = None,
        resume_bench_id: str | None = None,
        progress_log: Path | None = None,
        seeds: int = 1,
        case_ids: list[str] | None = None,
    ) -> ComparisonReport:
        if seeds < 1:
            raise ValueError("seeds must be at least 1")
        loader = case_loader or LocalYamlCaseLoader(cases_dir)
        cases = loader.load(limit=None if case_ids else limit)
        if case_ids:
            requested = set(case_ids)
            cases = [case for case in cases if case.id in requested][:limit]
            missing = requested - {case.id for case in cases}
            if missing:
                raise ValueError(f"Unknown benchmark case ids: {sorted(missing)}")
        if suite != "local":
            cases = [c for c in cases if c.suite == suite] or cases

        if resume_bench_id:
            bench_id = resume_bench_id
            scores = self.store.list_scores(bench_id)
            done = self.store.scored_pairs(bench_id)
            oracle_spent = sum(
                (s.subject_cost_usd for s in scores if s.subject_id == "frontier_reference"),
                Decimal("0"),
            )
            pairwise_results = self.store.list_pairwise(bench_id)
        else:
            bench_id = f"bench-{uuid.uuid4().hex[:12]}"
            scores = []
            done = set()
            oracle_spent = Decimal("0")
            pairwise_results = []

        work_root = self.pf_root / "benches" / bench_id
        work_root.mkdir(parents=True, exist_ok=True)

        configs = default_subject_configs()
        all_lessons = []
        artifacts_by_key: dict[tuple[str, int, str], object] = {}
        summaries_by_key: dict[tuple[str, int, str], str] = {}
        pairwise_done = {
            (str(result["case_id"]), int(result["seed"])) for result in pairwise_results
        }
        case_by_id = {c.id: c for c in cases}

        def _progress(msg: str) -> None:
            line = f"{msg}\n"
            if progress_log is not None:
                progress_log.parent.mkdir(parents=True, exist_ok=True)
                with progress_log.open("a", encoding="utf-8") as fh:
                    fh.write(line)
                    fh.flush()
            print(msg, flush=True)

        _progress(
            f"bench={bench_id} cases={len(cases)} subjects={subjects} "
            f"seeds={seeds} already_scored={len(done)} resume={bool(resume_bench_id)}"
        )

        for case in cases:
            self.store.upsert_case(case.id, case.suite, case.model_dump(mode="json"))
            for subject_id in subjects:
                for seed in range(seeds):
                    if subject_id not in self._runners:
                        continue
                    if (case.id, subject_id, seed) in done:
                        _progress(
                            f"skip {case.id}/{subject_id}/seed-{seed} (already scored)"
                        )
                        continue
                    if subject_id == "frontier_reference" and oracle_spent >= oracle_budget_usd:
                        continue
                    seeded_case = case.model_copy(
                        update={"metadata": {**case.metadata, "benchmark_seed": seed}}
                    )
                    _progress(f"start {case.id}/{subject_id}/seed-{seed}")
                    cfg = configs[subject_id]
                    subject_dir = work_root / case.id / subject_id / f"seed-{seed}"
                    artifact = self._runners[subject_id].run(
                        seeded_case,
                        config=cfg,
                        gateway=self.gateway,
                        work_dir=subject_dir,
                    )
                    repo = subject_dir / "repo"
                    det = run_deterministic_checks(
                        seeded_case,
                        artifact,
                        repository=repo if repo.exists() else None,
                        registered_commands=self.app_config.policies.registered_commands,
                    )
                    summary = deterministic_summary(det)
                    artifact_key = (case.id, seed, subject_id)
                    artifacts_by_key[artifact_key] = artifact
                    summaries_by_key[artifact_key] = summary
                    if subject_id == "frontier_reference":
                        remaining = float(oracle_budget_usd - oracle_spent)
                        if remaining <= 0:
                            continue
                    judge_result = self.judge.score(
                        case=seeded_case,
                        artifact=artifact,
                        deterministic_summary=summary,
                    )
                    score = merge_scores(
                        case=seeded_case,
                        artifact=artifact,
                        det_results=det,
                        judge=judge_result,
                        usable_threshold=self.usable_threshold,
                        seed=seed,
                    )
                    scores.append(score)
                    self.store.record_score(bench_id=bench_id, score=score)
                    done.add((case.id, subject_id, seed))
                    _progress(
                        f"done {case.id}/{subject_id}/seed-{seed} "
                        f"usable={score.final_usable} q={score.normalized_quality:.2f} "
                        f"cost={score.subject_cost_usd}"
                    )
                    if subject_id == "frontier_reference":
                        oracle_spent += score.subject_cost_usd
                    if not judge_result.is_mock and isinstance(self.judge, LLMJudge):
                        if self.judge.model_profile == "frontier_oracle":
                            oracle_spent += score.judge_cost_usd
                    pair_key = (case.id, seed)
                    orchestration_key = (case.id, seed, "full_orchestration")
                    baseline_key = (case.id, seed, "single_agent_baseline")
                    if (
                        pair_key not in pairwise_done
                        and orchestration_key in artifacts_by_key
                        and baseline_key in artifacts_by_key
                    ):
                        pair_result, label_map = self.judge.pairwise(
                            case=seeded_case,
                            artifact_a=artifacts_by_key[orchestration_key],  # type: ignore[arg-type]
                            artifact_b=artifacts_by_key[baseline_key],  # type: ignore[arg-type]
                            deterministic_summary=(
                                summaries_by_key[orchestration_key]
                                + "\n---\n"
                                + summaries_by_key[baseline_key]
                            ),
                            rng=random.Random(seed),
                        )
                        preference = pair_result.verdict.pairwise_preference or "tie"
                        winner = label_map.get(preference, "tie")
                        pair_payload = {
                            "case_id": case.id,
                            "seed": seed,
                            "label_map": label_map,
                            "preference": preference,
                            "winner": winner,
                            "uncertain": pair_result.verdict.uncertain,
                            "summary": pair_result.verdict.summary,
                            "judge_cost_usd": str(pair_result.usage.estimated_cost_usd),
                        }
                        self.store.record_pairwise(
                            bench_id=bench_id,
                            case_id=case.id,
                            seed=seed,
                            result=pair_payload,
                        )
                        pairwise_results.append(pair_payload)
                        pairwise_done.add(pair_key)

        for score in scores:
            case = case_by_id.get(score.case_id)
            if case is not None:
                all_lessons.extend(extract_lessons(case=case, score=score))

        report = build_comparison(
            bench_id=bench_id,
            scores=scores,
            pairwise_results=pairwise_results,
            cases=cases,
        )
        # Override oracle cost with tracked spend when available
        if oracle_spent > 0:
            report = report.model_copy(update={"oracle_cost_usd": oracle_spent})
        self.store.save_bench(report)
        out_dir = self.pf_root / "bench-reports"
        self.store.write_reports(report, out_dir)
        write_lesson_candidates(all_lessons, self.pf_root / "lessons" / "candidates" / bench_id)
        # Sidecar index for lessons
        (self.pf_root / "benches" / bench_id / "meta.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        return report


def build_judge(
    gateway: ModelGateway,
    *,
    judge_profile: str = "grok_judge",
    force_mock: bool = False,
    max_cost_usd: float | None = None,
    db: Database | None = None,
    content_dir: Path | None = None,
) -> Judge:
    if force_mock or isinstance(gateway, MockGateway):
        return MockJudge()
    if db is not None:
        gateway = _instrument_gateway(
            gateway, db, content_dir or Path(".product-factory/content/bench")
        )
    return LLMJudge(gateway, model_profile=judge_profile, max_cost_usd=max_cost_usd)
