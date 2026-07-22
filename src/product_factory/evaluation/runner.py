"""Legacy thin evaluation runner — delegates to BenchmarkRunner.

Prefer `product_factory.evaluation.bench.BenchmarkRunner` for LLM-judge benchmarks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.loader import load_eval_cases
from product_factory.gateway.base import ModelGateway

BASELINE_CONFIGS = {
    "single_strong": {"description": "Single strong agent baseline"},
    "single_coding": {"description": "Single coding specialist"},
    "multi_agent": {"description": "Full multi-model MVP"},
    "multi_no_reviewer": {"description": "Multi-model without reviewer"},
    "frontier_oracle": {"description": "Frontier reference (sampled)"},
}

SUBJECT_FOR_CONFIG = {
    "single_strong": ["single_agent_baseline"],
    "single_coding": ["agent_isolation"],
    "multi_agent": ["full_orchestration"],
    "multi_no_reviewer": ["full_orchestration"],
    "frontier_oracle": ["frontier_reference"],
}


def load_cases(cases_dir: Path) -> list[EvalCase]:
    return load_eval_cases(cases_dir)


def quality_efficiency(quality_score: float, total_cost_usd: Any) -> float:
    from decimal import Decimal

    from product_factory.domain.budgets import parse_decimal

    floor = Decimal("0.01")
    return float(Decimal(str(quality_score)) / max(parse_decimal(total_cost_usd), floor))


def run_evaluation(
    *,
    cases_dir: Path,
    app_config: AppConfig,
    gateway: ModelGateway,
    config_name: str = "multi_agent",
    limit: int = 10,
    use_mock: bool = True,
) -> dict[str, Any]:
    """Run a lightweight bench and return a dict compatible with the old CLI."""
    subjects = SUBJECT_FOR_CONFIG.get(config_name, ["full_orchestration"])
    judge = build_judge(gateway, force_mock=use_mock)
    runner = BenchmarkRunner(
        app_config=app_config,
        gateway=gateway,
        judge=judge,
        use_deterministic_planner=use_mock,
    )
    report = runner.run(
        cases_dir=cases_dir,
        subjects=subjects,
        limit=limit,
        suite="local",
    )
    successes = sum(1 for s in report.scores if s.final_usable)
    n_cases = max(len(report.case_ids), 1)
    return {
        "config": config_name,
        "config_description": BASELINE_CONFIGS.get(config_name, {}),
        "bench_id": report.bench_id,
        "cases_run": len(report.case_ids),
        "successes": successes,
        "success_rate": successes / n_cases,
        "total_cost_usd": str(report.subject_cost_usd + report.oracle_cost_usd),
        "judge_cost_usd": str(report.judge_cost_usd),
        "oracle_cost_usd": str(report.oracle_cost_usd),
        "quality_efficiency": (
            (sum(s.normalized_quality for s in report.scores) / max(len(report.scores), 1))
            / max(float(report.subject_cost_usd or 0) or 0.01, 0.01)
        ),
        "results": [
            {
                "case_id": s.case_id,
                "subject_id": s.subject_id,
                "status": "usable" if s.final_usable else "not_usable",
                "ok": s.final_usable,
                "cost_usd": str(s.subject_cost_usd),
                "quality": s.normalized_quality,
            }
            for s in report.scores
        ],
    }
