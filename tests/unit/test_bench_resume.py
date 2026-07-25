"""Resume skips already-scored case/subject pairs."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from product_factory.config.loader import load_config
from product_factory.evaluation.bench import BenchmarkRunner
from product_factory.evaluation.deterministic import EvaluationScore
from product_factory.gateway.mock import MockGateway


def test_resume_skips_scored_pairs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Minimal project layout
    (tmp_path / "config").mkdir()
    root = Path(__file__).resolve().parents[2]
    for name in ("models.yaml", "policies.yaml", "workflows.yaml"):
        src = root / "config" / name
        if src.exists():
            (tmp_path / "config" / name).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8"
            )
    cases = tmp_path / "cases"
    cases.mkdir()
    # Copy two tiny cases from repo if present, else write stubs
    for case_id, wf in (("adv_budget", "architecture"), ("code_cache", "architecture")):
        (cases / f"{case_id}.yaml").write_text(
            f"id: {case_id}\nworkflow_type: {wf}\nrequest: test {case_id}\n"
            "acceptance_criteria: [ok]\n",
            encoding="utf-8",
        )

    config = load_config(tmp_path)
    runner = BenchmarkRunner(
        app_config=config,
        gateway=MockGateway(),
        data_dir=tmp_path / ".product-factory",
        use_deterministic_planner=True,
    )
    first = runner.run(
        cases_dir=cases,
        subjects=["full_orchestration"],
        limit=1,
        progress_log=tmp_path / "p1.log",
    )
    assert len(first.case_ids) == 1
    # Pretend second case already partial: only record one score for resume path
    bench_id = first.bench_id
    existing = runner.store.list_scores(bench_id)
    assert len(existing) == 1

    # Seed a fake score for the other case so resume skips both subjects we mark
    runner.store.record_score(
        bench_id=bench_id,
        score=EvaluationScore(
            case_id="code_cache",
            subject_id="full_orchestration",
            deterministic_pass=True,
            normalized_quality=0.5,
            subject_cost_usd=Decimal("0"),
            judge_cost_usd=Decimal("0"),
            final_usable=False,
            summary="seeded",
        ),
    )
    report = runner.run(
        cases_dir=cases,
        subjects=["full_orchestration"],
        limit=2,
        resume_bench_id=bench_id,
        progress_log=tmp_path / "p2.log",
    )
    assert report.bench_id == bench_id
    assert set(report.case_ids) == {"adv_budget", "code_cache"}
    log = (tmp_path / "p2.log").read_text(encoding="utf-8")
    assert "skip adv_budget/full_orchestration" in log
    assert "skip code_cache/full_orchestration" in log
