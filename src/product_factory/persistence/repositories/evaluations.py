"""Evaluation aggregate repository (SD3.A).

Evaluation schema is owned by versioned migrations. Dual-write to
``evaluation_runs`` remains for compatibility until an export/reader path
is verified and dual writes are explicitly retired.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from product_factory.persistence.repositories.base import AggregateRepository, synchronized

if TYPE_CHECKING:
    from product_factory.evaluation.compare import ComparisonReport
    from product_factory.evaluation.deterministic import EvaluationScore


class EvaluationRepository(AggregateRepository):
    @synchronized
    def upsert_case(self, case_id: str, suite: str, case_json: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO evaluation_cases (case_id, suite, case_json)
            VALUES (?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET suite=excluded.suite, case_json=excluded.case_json
            """,
            (case_id, suite, json.dumps(case_json, default=str)),
        )
        self._conn.commit()

    @synchronized
    def record_score(self, *, bench_id: str, score: EvaluationScore) -> None:
        score_id = f"{bench_id}:{score.case_id}:{score.subject_id}:{score.seed}"
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
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
                now,
            ),
        )
        # Compatibility dual-write (SD3.A): keep until export reader verified.
        self._conn.execute(
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
                now,
            ),
        )
        self._conn.commit()

    def list_scores(self, bench_id: str) -> list[Any]:
        from product_factory.evaluation.deterministic import EvaluationScore

        def _read(conn: Any) -> list[Any]:
            rows = conn.execute(
                """
                SELECT score_json FROM evaluation_scores
                WHERE bench_id = ?
                ORDER BY created_at ASC
                """,
                (bench_id,),
            ).fetchall()
            return [EvaluationScore.model_validate_json(r["score_json"]) for r in rows]

        return self._actor.run(_read)

    def scored_pairs(self, bench_id: str) -> set[tuple[str, str, int]]:
        def _read(conn: Any) -> set[tuple[str, str, int]]:
            rows = conn.execute(
                """
                SELECT case_id, subject_id, seed FROM evaluation_scores WHERE bench_id = ?
                """,
                (bench_id,),
            ).fetchall()
            return {(str(r["case_id"]), str(r["subject_id"]), int(r["seed"] or 0)) for r in rows}

        return self._actor.run(_read)

    @synchronized
    def save_bench(self, report: ComparisonReport) -> None:
        self._conn.execute(
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
        self._conn.commit()

    @synchronized
    def record_pairwise(
        self, *, bench_id: str, case_id: str, seed: int, result: dict[str, Any]
    ) -> None:
        pair_id = f"{bench_id}:{case_id}:{seed}:orch-v-single"
        self._conn.execute(
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
        self._conn.commit()

    def list_pairwise(self, bench_id: str) -> list[dict[str, Any]]:
        def _read(conn: Any) -> list[dict[str, Any]]:
            rows = conn.execute(
                "SELECT result_json FROM evaluation_pairwise WHERE bench_id = ? ORDER BY case_id, seed",
                (bench_id,),
            ).fetchall()
            return [json.loads(row["result_json"]) for row in rows]

        return self._actor.run(_read)

    def get_bench(self, bench_id: str) -> dict[str, Any] | None:
        def _read(conn: Any) -> dict[str, Any] | None:
            row = conn.execute(
                "SELECT report_json FROM evaluation_benches WHERE bench_id = ?",
                (bench_id,),
            ).fetchone()
            if not row:
                return None
            return json.loads(row["report_json"])

        return self._actor.run(_read)
