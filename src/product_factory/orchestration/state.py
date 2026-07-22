"""LangGraph run state."""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, Literal, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    request: dict[str, Any]
    repository_snapshot: dict[str, Any]
    plan: dict[str, Any] | None
    compiler_errors: list[dict[str, Any]]
    task_specs: dict[str, dict[str, Any]]
    task_status: dict[str, str]
    task_results: Annotated[list[dict[str, Any]], add]
    findings: Annotated[list[dict[str, Any]], add]
    events: Annotated[list[dict[str, Any]], add]
    artifact_refs: dict[str, dict[str, Any]]
    budget: dict[str, Any]
    usage: dict[str, Any]
    plan_attempt: int
    repair_count: int
    no_progress_count: int
    pending_approvals: list[dict[str, Any]]
    final_status: Literal[
        "initializing",
        "planning",
        "plan_rejected",
        "executing",
        "validating",
        "repairing",
        "awaiting_approval",
        "completed",
        "failed",
        "blocked",
        "budget_exhausted",
    ]
    patch_text: str
    architecture_markdown: str
    validation_results: list[dict[str, Any]]
    workflow_type: str
    run_dir: str
    base_commit: str
    previous_patch_fp: str
    previous_finding_ids: list[str]
