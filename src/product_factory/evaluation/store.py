"""Benchmark persistence helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from product_factory.evaluation.compare import ComparisonReport
from product_factory.evaluation.deterministic import EvaluationScore
from product_factory.persistence.database import Database


class EvalStore:
    """Evaluation façade over ``Database.evaluations`` (no direct ``db.conn``)."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._repo = db.evaluations

    def upsert_case(self, case_id: str, suite: str, case_json: dict[str, Any]) -> None:
        self._repo.upsert_case(case_id, suite, case_json)

    def record_score(self, *, bench_id: str, score: EvaluationScore) -> None:
        self._repo.record_score(bench_id=bench_id, score=score)

    def list_scores(self, bench_id: str) -> list[EvaluationScore]:
        return self._repo.list_scores(bench_id)

    def scored_pairs(self, bench_id: str) -> set[tuple[str, str, int]]:
        return self._repo.scored_pairs(bench_id)

    def save_bench(self, report: ComparisonReport) -> None:
        self._repo.save_bench(report)

    def record_pairwise(
        self, *, bench_id: str, case_id: str, seed: int, result: dict[str, Any]
    ) -> None:
        self._repo.record_pairwise(
            bench_id=bench_id, case_id=case_id, seed=seed, result=result
        )

    def list_pairwise(self, bench_id: str) -> list[dict[str, Any]]:
        return self._repo.list_pairwise(bench_id)

    def get_bench(self, bench_id: str) -> dict[str, Any] | None:
        return self._repo.get_bench(bench_id)

    def write_reports(self, report: ComparisonReport, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{report.bench_id}.json"
        md_path = output_dir / f"{report.bench_id}.md"
        json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        from product_factory.evaluation.compare import report_to_markdown

        md_path.write_text(report_to_markdown(report), encoding="utf-8")
        return json_path, md_path
