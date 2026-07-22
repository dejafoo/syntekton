"""Contract tests for benchmark runner with MockJudge."""

from __future__ import annotations

from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.evaluation.adapters.base import ExternalSuiteCaseLoader
from product_factory.evaluation.bench import BenchmarkRunner, build_judge
from product_factory.gateway.mock import MockGateway


def test_bench_smoke_two_subjects(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    config = load_config(root)
    gateway = MockGateway()
    runner = BenchmarkRunner(
        app_config=config,
        gateway=gateway,
        judge=build_judge(gateway, force_mock=True),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    report = runner.run(
        cases_dir=root / "tests" / "eval_cases",
        subjects=["full_orchestration", "single_agent_baseline"],
        limit=2,
    )
    assert report.bench_id.startswith("bench-")
    assert len(report.scores) >= 2
    assert (tmp_path / ".product-factory" / "bench-reports" / f"{report.bench_id}.json").exists()
    lessons = tmp_path / ".product-factory" / "lessons" / "candidates" / report.bench_id
    assert lessons.exists()


def test_external_suite_loader_maps_to_eval_case() -> None:
    loader = ExternalSuiteCaseLoader(
        records=[
            {
                "id": "ext-1",
                "prompt": "Fix the bug",
                "workflow_type": "code_change",
                "tags": ["deepswe-like"],
                "acceptance_criteria": ["tests pass"],
            }
        ]
    )
    cases = loader.load()
    assert len(cases) == 1
    assert cases[0].suite == "external"
    assert cases[0].request == "Fix the bug"
