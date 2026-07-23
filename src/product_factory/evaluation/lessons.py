"""Lesson candidate export for human-gated prompt/skill improvement."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from product_factory.evaluation.cases import EvalCase
from product_factory.evaluation.deterministic import EvaluationScore

LessonStatus = Literal["proposed", "accepted", "rejected", "promoted"]
LessonCategory = Literal[
    "planner_delegation",
    "skill_gap",
    "prompt_contract",
    "review_quality",
    "other",
]

ACTIONABLE_SUBJECT_PREFIXES = (
    "full_orchestration",
    "orchestration_",
    "seeded_repair",
)


class LessonCandidate(BaseModel):
    id: str
    status: LessonStatus = "proposed"
    category: LessonCategory
    case_id: str
    subject_id: str
    summary: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggested_action: str = ""
    theme: str = "other"
    source_bench_id: str | None = None
    actionable: bool = False
    promoted_refs: list[str] = Field(default_factory=list)
    decision_note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def is_actionable_subject(subject_id: str) -> bool:
    return any(subject_id.startswith(prefix) for prefix in ACTIONABLE_SUBJECT_PREFIXES)


def derive_theme(
    *,
    deterministic_results: list[Any] | None = None,
    weak_dimensions: list[str] | None = None,
    summary: str = "",
) -> str:
    """Stable theme label for triage (validator message / weak dims)."""
    messages: list[str] = []
    for result in deterministic_results or []:
        if hasattr(result, "status"):
            if getattr(result, "status", None) in {"fail", "error"}:
                messages.append(str(getattr(result, "message", "") or ""))
                messages.append(str(getattr(result, "validator_id", "") or ""))
        elif isinstance(result, dict):
            if result.get("status") in {"fail", "error"}:
                messages.append(str(result.get("message") or ""))
                messages.append(str(result.get("validator_id") or ""))
    blob = " ".join(messages + [summary]).lower()
    if "must-cover" in blob or "must_cover" in blob or "architecture_must_cover" in blob:
        return "must_cover"
    if "architecture" in blob and ("incomplete" in blob or "section" in blob):
        return "arch_incomplete"
    if "boilerplate" in blob or "template" in blob:
        return "arch_boilerplate"
    if "artifact_empty" in blob or "empty" in blob and "artifact" in blob:
        return "patch_empty"
    if "patch" in blob and ("invalid" in blob or "apply" in blob):
        return "patch_invalid"
    if "live_fallback" in blob:
        return "live_fallback"
    if "subject_error" in blob or "no_progress" in blob:
        return "runtime_error"
    weak = weak_dimensions or []
    if "evidence_quality" in weak:
        return "weak_evidence"
    if "correctness" in weak:
        return "weak_correctness"
    if "test_quality" in weak:
        return "weak_tests"
    if "scope_discipline" in weak:
        return "weak_scope"
    if weak:
        return "weak_dimensions"
    return "other"


def extract_lessons(
    *,
    case: EvalCase,
    score: EvaluationScore,
    source_bench_id: str | None = None,
) -> list[LessonCandidate]:
    lessons: list[LessonCandidate] = []
    actionable = is_actionable_subject(score.subject_id)
    if not score.final_usable:
        weak = [n for n, s in score.dimension_scores.items() if s <= 2]
        if not score.deterministic_pass:
            theme = derive_theme(
                deterministic_results=score.deterministic_results,
                summary="Deterministic validation failed",
            )
            lessons.append(
                LessonCandidate(
                    id=f"lesson-{uuid.uuid4().hex[:10]}",
                    category="prompt_contract",
                    case_id=case.id,
                    subject_id=score.subject_id,
                    summary="Deterministic validation failed",
                    evidence={"validators": [r.model_dump() for r in score.deterministic_results]},
                    suggested_action="Tighten worker prompt/skills for validation gates",
                    theme=theme,
                    source_bench_id=source_bench_id,
                    actionable=actionable,
                )
            )
        if weak:
            category: LessonCategory
            if "scope_discipline" in weak or score.subject_id == "full_orchestration":
                category = "planner_delegation" if "scope_discipline" in weak else "skill_gap"
            else:
                category = "skill_gap"
            if "evidence_quality" in weak:
                category = "review_quality"
            theme = derive_theme(weak_dimensions=weak, summary=f"Weak dimensions: {', '.join(weak)}")
            lessons.append(
                LessonCandidate(
                    id=f"lesson-{uuid.uuid4().hex[:10]}",
                    category=category,
                    case_id=case.id,
                    subject_id=score.subject_id,
                    summary=f"Weak dimensions: {', '.join(weak)}",
                    evidence={"dimension_scores": score.dimension_scores},
                    suggested_action="Draft skill or prompt revision targeting weak dimensions",
                    theme=theme,
                    source_bench_id=source_bench_id,
                    actionable=actionable,
                )
            )
    return lessons


def write_lesson_candidates(
    lessons: list[LessonCandidate],
    output_dir: Path,
    *,
    source_bench_id: str | None = None,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for lesson in lessons:
        if source_bench_id and not lesson.source_bench_id:
            lesson = lesson.model_copy(update={"source_bench_id": source_bench_id})
        path = output_dir / f"{lesson.id}.json"
        path.write_text(lesson.model_dump_json(indent=2) + "\n", encoding="utf-8")
        written.append(path)
    index = output_dir / "index.jsonl"
    with index.open("a", encoding="utf-8") as fh:
        for lesson in lessons:
            if source_bench_id and not lesson.source_bench_id:
                lesson = lesson.model_copy(update={"source_bench_id": source_bench_id})
            fh.write(json.dumps(lesson.model_dump(mode="json"), default=str) + "\n")
    return written


def candidates_dir(pf_root: Path, bench_id: str) -> Path:
    return pf_root / "lessons" / "candidates" / bench_id


def load_lesson(path: Path) -> LessonCandidate:
    return LessonCandidate.model_validate_json(path.read_text(encoding="utf-8"))


def find_lesson(pf_root: Path, lesson_id: str, *, bench_id: str | None = None) -> tuple[Path, LessonCandidate]:
    roots: list[Path]
    if bench_id:
        roots = [candidates_dir(pf_root, bench_id)]
    else:
        root = pf_root / "lessons" / "candidates"
        roots = sorted(p for p in root.iterdir() if p.is_dir()) if root.exists() else []
    for directory in roots:
        path = directory / f"{lesson_id}.json"
        if path.exists():
            return path, load_lesson(path)
    raise FileNotFoundError(f"Lesson not found: {lesson_id}")


def list_lessons(
    pf_root: Path,
    *,
    bench_id: str,
    orch_only: bool = True,
    status: LessonStatus | None = None,
    theme: str | None = None,
) -> list[LessonCandidate]:
    directory = candidates_dir(pf_root, bench_id)
    if not directory.exists():
        return []
    lessons: list[LessonCandidate] = []
    for path in sorted(directory.glob("lesson-*.json")):
        lesson = load_lesson(path)
        if orch_only and not lesson.actionable and not is_actionable_subject(lesson.subject_id):
            continue
        if status and lesson.status != status:
            continue
        if theme and lesson.theme != theme:
            continue
        lessons.append(lesson)
    return lessons


def summarize_lessons(lessons: list[LessonCandidate]) -> dict[str, Any]:
    return {
        "count": len(lessons),
        "by_status": dict(Counter(lesson.status for lesson in lessons)),
        "by_category": dict(Counter(lesson.category for lesson in lessons)),
        "by_theme": dict(Counter(lesson.theme for lesson in lessons)),
        "by_case": dict(Counter(lesson.case_id for lesson in lessons)),
        "by_subject": dict(Counter(lesson.subject_id for lesson in lessons)),
    }


def _append_decision(directory: Path, payload: dict[str, Any]) -> None:
    path = directory / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")


def update_lesson_status(
    pf_root: Path,
    lesson_id: str,
    *,
    status: LessonStatus,
    note: str = "",
    bench_id: str | None = None,
    promoted_refs: list[str] | None = None,
) -> LessonCandidate:
    path, lesson = find_lesson(pf_root, lesson_id, bench_id=bench_id)
    updates: dict[str, Any] = {"status": status, "decision_note": note}
    if promoted_refs is not None:
        updates["promoted_refs"] = promoted_refs
    updated = lesson.model_copy(update=updates)
    path.write_text(updated.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _append_decision(
        path.parent,
        {
            "at": datetime.now(UTC).isoformat(),
            "lesson_id": lesson_id,
            "from_status": lesson.status,
            "to_status": status,
            "note": note,
            "promoted_refs": promoted_refs or [],
        },
    )
    return updated


def reject_lessons_matching(
    pf_root: Path,
    *,
    bench_id: str,
    filter_name: str,
    note: str = "",
) -> list[LessonCandidate]:
    """Bulk-reject non-actionable noise (e.g. baseline/frontier)."""
    directory = candidates_dir(pf_root, bench_id)
    updated: list[LessonCandidate] = []
    for path in sorted(directory.glob("lesson-*.json")):
        lesson = load_lesson(path)
        if lesson.status != "proposed":
            continue
        subject = lesson.subject_id
        match = False
        if filter_name == "baseline":
            match = subject.startswith("single_agent") or subject == "frontier_reference"
        elif filter_name == "non_orch":
            match = not is_actionable_subject(subject)
        if match:
            updated.append(
                update_lesson_status(
                    pf_root,
                    lesson.id,
                    status="rejected",
                    note=note or f"bulk reject filter={filter_name}",
                    bench_id=bench_id,
                )
            )
    return updated


def _bump_semver(version: str) -> str:
    parts = version.strip().split(".")
    if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
        major, minor, patch = (int(parts[0]), int(parts[1]), int(parts[2]))
        return f"{major}.{minor}.{patch + 1}"
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{int(parts[1]) + 1}"
    suffix = re.search(r"(\d+)$", version)
    if suffix:
        n = int(suffix.group(1)) + 1
        return version[: suffix.start(1)] + str(n)
    return f"{version}.1"


def bump_skill_version(skill_dir: Path) -> tuple[str, str]:
    """Bump manifest.yaml patch version. Returns (skill_id, new_version)."""
    manifest_path = skill_dir / "manifest.yaml"
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    old = str(data.get("version") or "1.0.0")
    new = _bump_semver(old)
    data["version"] = new
    manifest_path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return str(data.get("id") or skill_dir.name), new


def promote_lessons(
    pf_root: Path,
    *,
    lesson_ids: list[str],
    files: list[Path],
    bump_skill_ids: list[str],
    project_root: Path,
    note: str = "",
    bench_id: str | None = None,
) -> dict[str, Any]:
    """Record human-authored promotion; bump skill versions; never invent skill text."""
    allowed_roots = [
        (project_root / "skills").resolve(),
        (project_root / "src" / "product_factory" / "context").resolve(),
        (project_root / "src" / "product_factory" / "validation").resolve(),
        (project_root / "src" / "product_factory" / "orchestration").resolve(),
    ]
    resolved_files: list[Path] = []
    for raw in files:
        path = raw if raw.is_absolute() else (project_root / raw)
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Promotion file missing: {path}")
        if not any(path == root or root in path.parents for root in allowed_roots):
            raise ValueError(f"File not under allowed promotion roots: {path}")
        resolved_files.append(path)

    skill_root = (project_root / "skills").resolve()
    bumped: dict[str, str] = {}
    for skill_id in bump_skill_ids:
        matches = list(skill_root.rglob("manifest.yaml"))
        found: Path | None = None
        for manifest in matches:
            data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            if str(data.get("id") or "") == skill_id:
                found = manifest.parent
                break
        if found is None:
            raise FileNotFoundError(f"Skill id not found: {skill_id}")
        sid, version = bump_skill_version(found)
        bumped[sid] = version

    promoted_refs = [str(p.relative_to(project_root)) for p in resolved_files] + [
        f"skill:{sid}@{ver}" for sid, ver in bumped.items()
    ]
    updated_lessons: list[str] = []
    for lesson_id in lesson_ids:
        update_lesson_status(
            pf_root,
            lesson_id,
            status="promoted",
            note=note or "human-gated promotion",
            bench_id=bench_id,
            promoted_refs=promoted_refs,
        )
        updated_lessons.append(lesson_id)

    promotions_dir = pf_root / "lessons" / "promotions"
    promotions_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ledger = {
        "at": datetime.now(UTC).isoformat(),
        "lesson_ids": updated_lessons,
        "files": [str(p) for p in resolved_files],
        "skill_versions": bumped,
        "note": note,
        "adr_007": "no automatic skill injection; human-authored files only",
    }
    ledger_path = promotions_dir / f"{stamp}.json"
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    ledger["ledger_path"] = str(ledger_path)
    return ledger
