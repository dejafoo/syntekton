"""Lesson candidate export for human-gated prompt/skill improvement."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import EvaluationScore


class LessonCandidate(BaseModel):
    id: str
    status: Literal["proposed", "accepted", "rejected", "promoted"] = "proposed"
    category: Literal[
        "planner_delegation",
        "skill_gap",
        "prompt_contract",
        "review_quality",
        "other",
    ]
    case_id: str
    subject_id: str
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def extract_lessons(
    *,
    case: EvalCase,
    score: EvaluationScore,
) -> list[LessonCandidate]:
    lessons: list[LessonCandidate] = []
    if not score.final_usable:
        weak = [n for n, s in score.dimension_scores.items() if s <= 2]
        if not score.deterministic_pass:
            lessons.append(
                LessonCandidate(
                    id=f"lesson-{uuid.uuid4().hex[:10]}",
                    category="prompt_contract",
                    case_id=case.id,
                    subject_id=score.subject_id,
                    summary="Deterministic validation failed",
                    evidence={"validators": [r.model_dump() for r in score.deterministic_results]},
                    suggested_action="Tighten worker prompt/skills for validation gates",
                )
            )
        if weak:
            category: Literal[
                "planner_delegation", "skill_gap", "prompt_contract", "review_quality", "other"
            ]
            if "scope_discipline" in weak or score.subject_id == "full_orchestration":
                category = "planner_delegation" if "scope_discipline" in weak else "skill_gap"
            else:
                category = "skill_gap"
            if "evidence_quality" in weak:
                category = "review_quality"
            lessons.append(
                LessonCandidate(
                    id=f"lesson-{uuid.uuid4().hex[:10]}",
                    category=category,
                    case_id=case.id,
                    subject_id=score.subject_id,
                    summary=f"Weak dimensions: {', '.join(weak)}",
                    evidence={"dimension_scores": score.dimension_scores},
                    suggested_action="Draft skill or prompt revision targeting weak dimensions",
                )
            )
    return lessons


def write_lesson_candidates(
    lessons: list[LessonCandidate],
    output_dir: Path,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for lesson in lessons:
        path = output_dir / f"{lesson.id}.json"
        path.write_text(lesson.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
    index = output_dir / "index.jsonl"
    with index.open("a", encoding="utf-8") as fh:
        for lesson in lessons:
            fh.write(json.dumps(lesson.model_dump(mode="json"), default=str) + "\n")
    return written
