"""Benchmark persistence helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_factory.evaluation.compare import ComparisonReport
from product_factory.evaluation.deterministic import EvaluationScore
from product_factory.persistence.database import Database

EVAL_SCHEMA_EXTRA = """
CREATE TABLE IF NOT EXISTS evaluation_cases (
    case_id TEXT PRIMARY KEY,
    suite TEXT NOT NULL,
    case_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_runs (
    eval_run_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    config_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_scores (
    score_id TEXT PRIMARY KEY,
    bench_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    seed INTEGER NOT NULL DEFAULT 0,
    score_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_benches (
    bench_id TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_pairwise (
    pair_id TEXT PRIMARY KEY,
    bench_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    seed INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class EvalStore:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.db.conn.executescript(EVAL_SCHEMA_EXTRA)
        columns = {
            str(r["name"])
            for r in self.db.conn.execute("PRAGMA table_info(evaluation_scores)").fetchall()
        }
        if "seed" not in columns:
            self.db.conn.execute(
                "ALTER TABLE evaluation_scores ADD COLUMN seed INTEGER NOT NULL DEFAULT 0"
            )
        self.db.conn.commit()

    def upsert_case(self, case_id: str, suite: str, case_json: dict[str, Any]) -> None:
        self.db.conn.execute(
            """
            INSERT INTO evaluation_cases (case_id, suite, case_json)
            VALUES (?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET suite=excluded.suite, case_json=excluded.case_json
            """,
            (case_id, suite, json.dumps(case_json, default=str)),
        )
        self.db.conn.commit()

    def record_score(self, *, bench_id: str, score: EvaluationScore) -> None:
        score_id = f"{bench_id}:{score.case_id}:{score.subject_id}:{score.seed}"
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO evaluation_scores
            (score_id, bench_id, case_id, subject_id, seed, score_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score_id,
                bench_id,
                score.case_id,
                score.subject_id,
                score.seed,
                score.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
        # Also keep legacy evaluation_runs row for compatibility
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO evaluation_runs
            (eval_run_id, case_id, config_name, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                score_id,
                score.case_id,
                score.subject_id,
                score.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.conn.commit()

    def list_scores(self, bench_id: str) -> list[EvaluationScore]:
        rows = self.db.conn.execute(
            """
            SELECT score_json FROM evaluation_scores
            WHERE bench_id = ?
            ORDER BY created_at ASC
            """,
            (bench_id,),
        ).fetchall()
        return [EvaluationScore.model_validate_json(r["score_json"]) for r in rows]

    def scored_pairs(self, bench_id: str) -> set[tuple[str, str, int]]:
        rows = self.db.conn.execute(
            """
            SELECT case_id, subject_id, seed FROM evaluation_scores WHERE bench_id = ?
            """,
            (bench_id,),
        ).fetchall()
        return {
            (str(r["case_id"]), str(r["subject_id"]), int(r["seed"] or 0)) for r in rows
        }

    def save_bench(self, report: ComparisonReport) -> None:
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO evaluation_benches (bench_id, report_json, created_at)
            VALUES (?, ?, ?)
            """,
            (
                report.bench_id,
                report.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.conn.commit()

    def record_pairwise(
        self, *, bench_id: str, case_id: str, seed: int, result: dict[str, Any]
    ) -> None:
        pair_id = f"{bench_id}:{case_id}:{seed}:orch-v-single"
        self.db.conn.execute(
            """
            INSERT OR REPLACE INTO evaluation_pairwise
            (pair_id, bench_id, case_id, seed, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                pair_id,
                bench_id,
                case_id,
                seed,
                json.dumps(result, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.db.conn.commit()

    def list_pairwise(self, bench_id: str) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            "SELECT result_json FROM evaluation_pairwise WHERE bench_id = ? ORDER BY case_id, seed",
            (bench_id,),
        ).fetchall()
        return [json.loads(row["result_json"]) for row in rows]

    def get_bench(self, bench_id: str) -> dict[str, Any] | None:
        row = self.db.conn.execute(
            "SELECT report_json FROM evaluation_benches WHERE bench_id = ?",
            (bench_id,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["report_json"])

    def write_reports(self, report: ComparisonReport, output_dir: Path) -> tuple[Path, Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / f"{report.bench_id}.json"
        md_path = output_dir / f"{report.bench_id}.md"
        json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        from product_factory.evaluation.compare import report_to_markdown

        md_path.write_text(report_to_markdown(report), encoding="utf-8")
        return json_path, md_path
