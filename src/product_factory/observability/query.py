"""Framework-neutral query service for observability read models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from product_factory.observability.contracts import (
    ArtifactView,
    HealthView,
    ModelInvocationView,
    PromptPackageView,
    RunSummary,
    TaskSummary,
    ToolCallView,
)
from product_factory.observability.recorder import capture_level_from_env
from product_factory.observability.stuck import derive_liveness
from product_factory.persistence.database import Database


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


class ObservabilityQueryService:
    def __init__(self, db: Database, *, data_dir: Path | None = None) -> None:
        self.db = db
        self.data_dir = data_dir

    def health(self) -> HealthView:
        last = self.db.last_event_at()
        return HealthView(
            status="ok",
            database_path=str(self.db.db_path),
            wal_mode=self.db.wal_enabled(),
            latest_seq=self.db.latest_seq(),
            last_event_at=last,
            writer_fresh=True,
            capture_level=capture_level_from_env(),
        )

    def list_runs(self, *, limit: int = 50, status: str | None = None) -> list[RunSummary]:
        return [self._run_summary(row) for row in self.db.list_runs(limit=limit, status=status)]

    def get_run(self, run_id: str) -> RunSummary | None:
        row = self.db.get_run(run_id)
        return self._run_summary(row) if row else None

    def _run_summary(self, row: dict[str, Any]) -> RunSummary:
        tasks = self.db.list_tasks(row["run_id"])
        counts: dict[str, int] = {}
        for t in tasks:
            counts[t["status"]] = counts.get(t["status"], 0) + 1
        req = _json_loads(row.get("request_json"), {})
        budget = (req.get("budget") if isinstance(req, dict) else {}) or {}
        latest_seq = self.db.latest_seq_for_run(row["run_id"])
        last_progress = row.get("last_progress_at") or row.get("updated_at")
        return RunSummary(
            run_id=row["run_id"],
            workflow_type=row["workflow_type"],
            status=row["status"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            base_commit=row.get("base_commit"),
            usage=_json_loads(row.get("usage_json"), {}),
            budget=budget if isinstance(budget, dict) else {},
            task_counts=counts,
            latest_seq=int(latest_seq),
            last_progress_at=last_progress,
            liveness=derive_liveness(status=row["status"], last_progress_at=last_progress),
            active_operation=row.get("active_operation"),
            error_count=self.db.count_error_events(row["run_id"]),
        )

    def list_tasks(self, run_id: str) -> list[TaskSummary]:
        deps = self.db.list_task_dependencies(run_id)
        dep_map: dict[str, list[str]] = {}
        for d in deps:
            dep_map.setdefault(d["task_id"], []).append(d["depends_on"])
        out: list[TaskSummary] = []
        for row in self.db.list_tasks(run_id):
            spec = _json_loads(row.get("spec_json"), {})
            result = _json_loads(row.get("result_json"), {})
            last = row.get("ended_at") or row.get("updated_at") or row.get("started_at")
            out.append(
                TaskSummary(
                    run_id=run_id,
                    task_id=row["task_id"],
                    capability=row["capability"],
                    status=row["status"],
                    title=spec.get("title"),
                    dependencies=dep_map.get(row["task_id"], spec.get("dependencies") or []),
                    model_profile=result.get("model_profile"),
                    agent_profile=None,
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                    attempt=int(row.get("attempt") or 1),
                    summary=result.get("summary"),
                    usage=result.get("usage") or {},
                    liveness=derive_liveness(status=row["status"], last_progress_at=last),
                    active_operation=row.get("active_operation"),
                )
            )
        return out

    def get_task(self, run_id: str, task_id: str) -> TaskSummary | None:
        for t in self.list_tasks(run_id):
            if t.task_id == task_id:
                return t
        return None

    def list_events(
        self,
        *,
        run_id: str | None = None,
        after_seq: int = 0,
        limit: int = 200,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self.db.list_events(
            run_id=run_id, after_seq=after_seq, limit=limit, types=types
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "seq": r["seq"],
                    "event_id": r["event_id"],
                    "schema_version": r["schema_version"],
                    "occurred_at": r["occurred_at"],
                    "recorded_at": r["recorded_at"],
                    "type": r["event_type"],
                    "run_id": r["run_id"],
                    "task_id": r["task_id"],
                    "request_id": r["request_id"],
                    "tool_call_id": r["tool_call_id"],
                    "trace_id": r["trace_id"],
                    "span_id": r["span_id"],
                    "parent_span_id": r["parent_span_id"],
                    "severity": r["severity"],
                    "summary": r["summary"],
                    "payload": _json_loads(r["payload_json"], {}),
                    "content_refs": _json_loads(r["content_refs_json"], []),
                }
            )
        return out

    def list_invocations(self, run_id: str) -> list[ModelInvocationView]:
        views: list[ModelInvocationView] = []
        for row in self.db.list_invocations(run_id):
            views.append(
                ModelInvocationView(
                    request_id=row["request_id"],
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    model_profile=row["model_profile"],
                    status=row["status"],
                    provider=row.get("provider"),
                    resolved_model_id=row.get("resolved_model_id"),
                    usage=_json_loads(row.get("usage_json"), {}),
                    response_hash=row.get("response_hash"),
                    prompt_package_hash=row.get("prompt_package_hash"),
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                    latency_ms=row.get("latency_ms"),
                    content_refs=_json_loads(row.get("content_refs_json"), []),
                )
            )
        return views

    def list_tool_calls(self, run_id: str) -> list[ToolCallView]:
        views: list[ToolCallView] = []
        for row in self.db.list_tool_calls(run_id):
            rec = _json_loads(row.get("record_json"), {})
            views.append(
                ToolCallView(
                    tool_call_id=row["tool_call_id"],
                    run_id=row["run_id"],
                    task_id=row["task_id"],
                    tool_name=row["tool_name"],
                    status=row.get("status") or "completed",
                    arguments_hash=rec.get("arguments_hash"),
                    duration_ms=rec.get("duration_ms"),
                    exit_status=rec.get("exit_status"),
                    output_artifact_ref=rec.get("output_artifact_ref"),
                    error=rec.get("error"),
                    started_at=row.get("started_at"),
                    ended_at=row.get("ended_at"),
                )
            )
        return views

    def list_validations(self, run_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "task_id": r["task_id"],
                "result": _json_loads(r["result_json"], {}),
            }
            for r in self.db.list_validator_results(run_id)
        ]

    def list_artifacts_for_run(self, run_id: str) -> list[ArtifactView]:
        task_ids = {t["task_id"] for t in self.db.list_tasks(run_id)}
        views: list[ArtifactView] = []
        for a in self.db.list_artifacts():
            if a.get("created_by_task_id") in task_ids or a.get("created_by_task_id") in {
                "compose",
                "plan",
                "system",
            }:
                views.append(
                    ArtifactView(
                        sha256=a["sha256"],
                        media_type=a["media_type"],
                        size_bytes=a["size_bytes"],
                        logical_name=a["logical_name"],
                        relative_path=a.get("relative_path"),
                        created_by_task_id=a.get("created_by_task_id"),
                        trust_level=a.get("trust_level", "generated"),
                        metadata=_json_loads(a.get("metadata_json"), {}),
                    )
                )
        # Also include filesystem prompts/artifacts under run dir when present
        return views

    def list_prompts(self, run_id: str) -> list[PromptPackageView]:
        if self.data_dir is None:
            return []
        prompt_dir = self.data_dir / "runs" / run_id / "prompts"
        if not prompt_dir.exists():
            return []
        out: list[PromptPackageView] = []
        for path in sorted(prompt_dir.glob("*.manifest.json")):
            manifest = _json_loads(path.read_text(encoding="utf-8"), {})
            task_id = path.name.replace(".manifest.json", "")
            out.append(
                PromptPackageView(
                    run_id=run_id,
                    task_id=task_id,
                    package_hash=str(manifest.get("package_hash") or manifest.get("hash") or ""),
                    manifest=manifest if isinstance(manifest, dict) else {},
                )
            )
        return out
