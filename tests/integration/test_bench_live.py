"""Opt-in live LLM judge smoke (requires PRODUCT_FACTORY_BENCH_LIVE=1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from product_factory.config.loader import load_config
from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.gateway.openrouter import OpenRouterGateway


@pytest.mark.integration
def test_live_bench_one_case() -> None:
    if os.environ.get("PRODUCT_FACTORY_BENCH_LIVE") != "1":
        pytest.skip("Set PRODUCT_FACTORY_BENCH_LIVE=1")
    if not os.environ.get("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    profiles = {
        name: {"model": p.model, "pricing": p.pricing, "provider": p.provider}
        for name, p in config.models.profiles.items()
    }
    gateway = OpenRouterGateway(profile_models=profiles)
    # Use cheap judge for live smoke if frontier is too expensive; still exercises LLMJudge path
    judge = build_judge(gateway, judge_profile="fast_worker", force_mock=False, max_cost_usd=0.1)
    runner = BenchmarkRunner(
        app_config=config,
        gateway=gateway,
        judge=judge,
        use_deterministic_planner=False,
    )
    report = runner.run(
        cases_dir=root / "tests" / "eval_cases",
        subjects=["single_agent_baseline"],
        limit=1,
        oracle_budget_usd=__import__("decimal").Decimal("0.50"),
    )
    assert report.scores
