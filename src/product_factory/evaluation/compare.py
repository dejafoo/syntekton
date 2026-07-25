"""Comparison reports across subjects."""

from __future__ import annotations

import random
from decimal import Decimal
from math import sqrt
from typing import Any

from pydantic import BaseModel, Field

from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import EvaluationScore


class ComparisonReport(BaseModel):
    bench_id: str
    case_ids: list[str]
    subjects: list[str]
    scores: list[EvaluationScore]
    matrix: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deltas: dict[str, Any] = Field(default_factory=dict)
    aggregates: dict[str, dict[str, Any]] = Field(default_factory=dict)
    paired_confidence_intervals: dict[str, Any] = Field(default_factory=dict)
    pairwise_results: list[dict[str, Any]] = Field(default_factory=list)
    pairwise_summary: dict[str, Any] = Field(default_factory=dict)
    strata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    seeds: int = 1
    oracle_cost_usd: Decimal = Field(default=Decimal("0"))
    subject_cost_usd: Decimal = Field(default=Decimal("0"))
    judge_cost_usd: Decimal = Field(default=Decimal("0"))


def build_comparison(
    *,
    bench_id: str,
    scores: list[EvaluationScore],
    pairwise_results: list[dict[str, Any]] | None = None,
    cases: list[EvalCase] | None = None,
) -> ComparisonReport:
    pairwise_results = pairwise_results or []
    case_ids = sorted({s.case_id for s in scores})
    subjects = sorted({s.subject_id for s in scores})
    matrix: dict[str, dict[str, Any]] = {}
    for case_id in case_ids:
        matrix[case_id] = {}
        for subject in subjects:
            hits = [s for s in scores if s.case_id == case_id and s.subject_id == subject]
            if hits:
                matrix[case_id][subject] = {
                    "usable": all(h.final_usable for h in hits),
                    "usable_rate": sum(h.final_usable for h in hits) / len(hits),
                    "artifact_rate": sum(h.artifact_produced for h in hits) / len(hits),
                    "patch_apply_rate": (
                        sum(h.patch_applies is True for h in hits)
                        / sum(h.patch_applies is not None for h in hits)
                        if any(h.patch_applies is not None for h in hits)
                        else None
                    ),
                    "behavioral_pass_rate": (
                        sum(h.behavioral_pass is True for h in hits)
                        / sum(h.behavioral_pass is not None for h in hits)
                        if any(h.behavioral_pass is not None for h in hits)
                        else None
                    ),
                    "quality": sum(h.normalized_quality for h in hits) / len(hits),
                    "overall": sum((h.judge_overall or 0) for h in hits) / len(hits),
                    "cost": str(sum((h.subject_cost_usd for h in hits), Decimal("0"))),
                    "seed_count": len(hits),
                }
            else:
                matrix[case_id][subject] = None

    deltas: dict[str, Any] = {}
    for case_id in case_ids:
        orch = [s for s in scores if s.case_id == case_id and s.subject_id == "full_orchestration"]
        single = [
            s for s in scores if s.case_id == case_id and s.subject_id == "single_agent_baseline"
        ]
        frontier = [
            s for s in scores if s.case_id == case_id and s.subject_id == "frontier_reference"
        ]
        entry: dict[str, Any] = {}
        if orch and single:
            entry["orch_minus_single_quality"] = sum(s.normalized_quality for s in orch) / len(
                orch
            ) - sum(s.normalized_quality for s in single) / len(single)
            entry["orch_minus_single_usable_rate"] = sum(s.final_usable for s in orch) / len(
                orch
            ) - sum(s.final_usable for s in single) / len(single)
        if orch and frontier:
            entry["orch_minus_frontier_quality"] = sum(s.normalized_quality for s in orch) / len(
                orch
            ) - sum(s.normalized_quality for s in frontier) / len(frontier)
        if entry:
            deltas[case_id] = entry

    subject_cost = sum((s.subject_cost_usd for s in scores), Decimal("0"))
    judge_cost = sum((s.judge_cost_usd for s in scores), Decimal("0")) + sum(
        (Decimal(str(result.get("judge_cost_usd", "0"))) for result in pairwise_results),
        Decimal("0"),
    )
    oracle_cost = sum(
        (s.subject_cost_usd for s in scores if s.subject_id == "frontier_reference"),
        Decimal("0"),
    )
    aggregates: dict[str, dict[str, Any]] = {}
    for subject in subjects:
        hits = [s for s in scores if s.subject_id == subject]
        usable_count = sum(s.final_usable for s in hits)
        total_cost = sum((s.subject_cost_usd for s in hits), Decimal("0"))
        aggregates[subject] = {
            "samples": len(hits),
            "usable_count": usable_count,
            "usable_rate": usable_count / len(hits) if hits else 0.0,
            "usable_rate_ci95": _wilson_interval(usable_count, len(hits)),
            "artifact_rate": sum(s.artifact_produced for s in hits) / len(hits) if hits else 0.0,
            "patch_apply_rate": _optional_rate([s.patch_applies for s in hits]),
            "behavioral_pass_rate": _optional_rate([s.behavioral_pass for s in hits]),
            "mean_valid_quality": (
                sum(s.normalized_quality for s in hits if s.deterministic_pass)
                / sum(s.deterministic_pass for s in hits)
                if any(s.deterministic_pass for s in hits)
                else None
            ),
            "total_subject_cost_usd": str(total_cost),
            "cost_per_usable_artifact": (str(total_cost / usable_count) if usable_count else None),
            "latency_per_usable_artifact_ms": (
                sum(score.subject_latency_ms for score in hits) / usable_count
                if usable_count
                else None
            ),
            "usable_artifacts_per_dollar": (
                float(Decimal(usable_count) / total_cost) if total_cost > 0 else None
            ),
        }
    paired_ci = _paired_usable_bootstrap(scores)
    seed_count = max((s.seed for s in scores), default=0) + 1
    wins = sum(r.get("winner") == "full_orchestration" for r in pairwise_results)
    losses = sum(r.get("winner") == "single_agent_baseline" for r in pairwise_results)
    decisive = wins + losses
    pairwise_summary = {
        "orchestration_wins": wins,
        "baseline_wins": losses,
        "ties": len(pairwise_results) - decisive,
        "orchestration_win_rate": wins / decisive if decisive else None,
        "win_rate_ci95": _wilson_interval(wins, decisive),
    }
    case_map = {case.id: case for case in (cases or [])}
    strata: dict[str, dict[str, Any]] = {}
    for score in scores:
        case = case_map.get(score.case_id)
        labels = [
            f"workflow:{case.workflow_type}" if case else "workflow:unknown",
            ("validation:behavioral" if case and case.smoke_commands else "validation:structural"),
            (
                f"complexity:{case.metadata.get('complexity', 'unspecified')}"
                if case
                else "complexity:unknown"
            ),
        ]
        if case and any(tag.startswith("adv") or tag == "adversarial" for tag in case.tags):
            labels.append("adversarial:true")
        for label in labels:
            key = f"{label}/{score.subject_id}"
            bucket = strata.setdefault(key, {"samples": 0, "usable": 0, "quality_total": 0.0})
            bucket["samples"] += 1
            bucket["usable"] += int(score.final_usable)
            bucket["quality_total"] += score.normalized_quality
    for bucket in strata.values():
        bucket["usable_rate"] = bucket["usable"] / bucket["samples"]
        bucket["mean_quality"] = bucket.pop("quality_total") / bucket["samples"]
    return ComparisonReport(
        bench_id=bench_id,
        case_ids=case_ids,
        subjects=subjects,
        scores=scores,
        matrix=matrix,
        deltas=deltas,
        aggregates=aggregates,
        paired_confidence_intervals=paired_ci,
        pairwise_results=pairwise_results,
        pairwise_summary=pairwise_summary,
        strata=strata,
        seeds=seed_count,
        oracle_cost_usd=oracle_cost,
        subject_cost_usd=subject_cost - oracle_cost,
        judge_cost_usd=judge_cost,
    )


def _optional_rate(values: list[bool | None]) -> float | None:
    present = [v for v in values if v is not None]
    return sum(v is True for v in present) / len(present) if present else None


def _wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    if total == 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denom
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _paired_usable_bootstrap(scores: list[EvaluationScore], samples: int = 2000) -> dict[str, Any]:
    by_key = {(s.case_id, s.seed, s.subject_id): s for s in scores}
    pairs: list[float] = []
    for case_id, seed, subject in sorted(by_key):
        if subject != "full_orchestration":
            continue
        orch = by_key[(case_id, seed, subject)]
        single = by_key.get((case_id, seed, "single_agent_baseline"))
        if single is not None:
            pairs.append(float(orch.final_usable) - float(single.final_usable))
    if not pairs:
        return {}
    rng = random.Random(0)
    boot = sorted(sum(rng.choice(pairs) for _ in pairs) / len(pairs) for _ in range(samples))
    return {
        "orch_minus_single_usable_rate": {
            "estimate": sum(pairs) / len(pairs),
            "ci95": [boot[int(samples * 0.025)], boot[int(samples * 0.975) - 1]],
            "paired_samples": len(pairs),
        }
    }


def report_to_markdown(report: ComparisonReport) -> str:
    lines = [
        f"# Benchmark {report.bench_id}",
        "",
        f"- Cases: {len(report.case_ids)}",
        f"- Subjects: {', '.join(report.subjects)}",
        f"- Subject cost (excl. oracle): ${report.subject_cost_usd}",
        f"- Oracle cost: ${report.oracle_cost_usd}",
        f"- Judge cost: ${report.judge_cost_usd}",
        f"- Seeds: {report.seeds}",
        "",
        "## Matrix",
        "",
    ]
    header = "| case | " + " | ".join(report.subjects) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(report.subjects)) + " |"
    lines.extend([header, sep])
    for case_id in report.case_ids:
        cells = []
        for subject in report.subjects:
            cell = report.matrix.get(case_id, {}).get(subject)
            if not cell:
                cells.append("-")
            else:
                mark = "OK" if cell["usable_rate"] >= 0.5 else "FAIL"
                cells.append(f"{mark} usable={cell['usable_rate']:.0%} q={cell['quality']:.2f}")
        lines.append(f"| {case_id} | " + " | ".join(cells) + " |")
    if report.deltas:
        lines.extend(["", "## Deltas (orchestration vs baselines)", ""])
        for case_id, delta in report.deltas.items():
            lines.append(f"- {case_id}: {delta}")
    if report.aggregates:
        lines.extend(["", "## Aggregate metrics", ""])
        for subject, aggregate in report.aggregates.items():
            lines.append(f"- {subject}: {aggregate}")
    if report.paired_confidence_intervals:
        lines.extend(["", "## Paired confidence intervals", ""])
        lines.append(str(report.paired_confidence_intervals))
    if report.pairwise_results:
        lines.extend(["", "## Blind pairwise results", "", str(report.pairwise_summary)])
    if report.strata:
        lines.extend(["", "## Stratified metrics", ""])
        for stratum, values in report.strata.items():
            lines.append(f"- {stratum}: {values}")
    return "\n".join(lines) + "\n"
