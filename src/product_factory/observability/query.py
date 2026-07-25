"""Framework-neutral query service for observability read models."""

from __future__ import annotations

import contextlib
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from product_factory.observability.contracts import (
    ArtifactView,
    CaptureLevel,
    ContentView,
    CostView,
    HealthView,
    LineageView,
    ModelInvocationView,
    PlanView,
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


_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_RUN_OUTPUT_FILES = frozenset({"plan.json", "compiler-report.json"})


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _usage_totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "latency_ms",
        "retries",
    )
    result: dict[str, Any] = {key: 0 for key in keys}
    result["estimated_cost_usd"] = Decimal("0")
    result["reported_cost_usd"] = Decimal("0")
    result["reported_count"] = 0
    for usage in items:
        for key in keys:
            result[key] += int(usage.get(key) or 0)
        result["estimated_cost_usd"] += _decimal(usage.get("estimated_cost_usd"))
        if usage.get("reported_cost_usd") is not None:
            result["reported_cost_usd"] += _decimal(usage.get("reported_cost_usd"))
            result["reported_count"] += 1
    return result


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
        ledger = _json_loads(row.get("budget_json"), {})
        if isinstance(ledger, dict) and isinstance(ledger.get("budget"), dict):
            budget = ledger["budget"]
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
        rows = self.db.list_events(run_id=run_id, after_seq=after_seq, limit=limit, types=types)
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
        run_dir = self._run_dir(run_id)
        if run_dir is None:
            return []
        task_ids = {t["task_id"] for t in self.db.list_tasks(run_id)}
        views: list[ArtifactView] = []
        for a in self.db.list_artifacts():
            # Artifact rows predate a run_id column. The run-local blob is the
            # authoritative ownership proof and avoids task-id collisions across
            # runs (for example every plan has a `plan` task).
            owned_blob = run_dir / "artifacts" / "blobs" / a["sha256"]
            if owned_blob.is_file() and (
                a.get("created_by_task_id") in task_ids
                or a.get("created_by_task_id")
                in {
                    "compose",
                    "plan",
                    "system",
                }
            ):
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

    def plan(self, run_id: str) -> PlanView:
        """Read only known coordinator output files; never browser-selected paths."""
        run_dir = self._run_dir(run_id)
        if run_dir is None:
            return PlanView(run_id=run_id)
        plan = self._read_run_output(run_dir, "plan.json")
        compiler = self._read_run_output(run_dir, "compiler-report.json")
        return PlanView(
            run_id=run_id,
            plan=plan if isinstance(plan, dict) else None,
            compiler=compiler if isinstance(compiler, dict) else None,
        )

    def lineage(self, run_id: str) -> LineageView:
        tasks = self.list_tasks(run_id)
        dependencies = {task.task_id: task.dependencies for task in tasks}
        task_by_id = {task.task_id: task for task in tasks}
        files: list[dict[str, Any]] = []
        run_dir = self._run_dir(run_id)
        if run_dir is not None:
            output = run_dir / "output"
            for path in sorted(output.glob("*-lineage.json")) if output.exists() else []:
                data = self._read_json(path)
                if isinstance(data, dict):
                    files.append({"name": path.name, "data": data})
        failed = [task for task in tasks if task.status in {"failed", "blocked"}]
        repairs: list[dict[str, Any]] = []
        for task in tasks:
            if task.capability != "repair":
                continue
            direct = [dep for dep in task.dependencies if dep in task_by_id]
            origin = next(
                (dep for dep in direct if task_by_id[dep].status in {"failed", "blocked"}), None
            )
            # Older runs can replace a failed task's dependency with its repair.
            # The best durable derivation available is the most recent failed task.
            if origin is None and failed:
                origin = failed[-1].task_id
            lineage = next(
                (entry["data"] for entry in files if entry["data"].get("task_id") == task.task_id),
                {},
            )
            repairs.append(
                {
                    "task_id": task.task_id,
                    "origin_task_id": origin,
                    "dependencies": task.dependencies,
                    "reason": task.title or task.summary,
                    "inherited_patch_fingerprint": lineage.get("pre_patch_fingerprint"),
                    "supersedes": [
                        candidate.task_id
                        for candidate in tasks
                        if candidate.task_id != task.task_id
                        and task.task_id in candidate.dependencies
                    ],
                }
            )
        return LineageView(run_id=run_id, dependencies=dependencies, repairs=repairs, files=files)

    def costs(self, run_id: str) -> CostView:
        invocations = self.db.list_invocations(run_id)
        parsed = [{**row, "usage": _json_loads(row.get("usage_json"), {})} for row in invocations]
        total = _usage_totals([row["usage"] for row in parsed])
        reported_count = int(total.pop("reported_count"))
        if reported_count == len(parsed) and parsed:
            basis = "reported"
        elif reported_count:
            basis = "mixed"
        else:
            basis = "estimated"
        for key in ("estimated_cost_usd", "reported_cost_usd"):
            total[key] = str(total[key])
        by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_model: dict[tuple[str | None, str | None, str], list[dict[str, Any]]] = defaultdict(list)
        for item in parsed:
            by_task[item["task_id"]].append(item["usage"])
            by_model[
                (item.get("provider"), item.get("resolved_model_id"), item["model_profile"])
            ].append(item["usage"])
        task_rows = [self._cost_row({"task_id": key}, values) for key, values in by_task.items()]
        model_rows = [
            self._cost_row(
                {"provider": key[0], "resolved_model_id": key[1], "model_profile": key[2]}, values
            )
            for key, values in by_model.items()
        ]
        row = self.db.get_run(run_id) or {}
        ledger = _json_loads(row.get("budget_json"), {})
        budget = ledger.get("budget") if isinstance(ledger, dict) else None
        if not isinstance(budget, dict):
            req = _json_loads(row.get("request_json"), {})
            budget = req.get("budget") if isinstance(req, dict) else {}
        max_cost = _decimal(budget.get("max_cost_usd") if isinstance(budget, dict) else 0)
        spend = _decimal(
            total["reported_cost_usd"] if basis == "reported" else total["estimated_cost_usd"]
        )
        total["remaining_budget_usd"] = str(max(max_cost - spend, Decimal("0")))
        return CostView(
            run_id=run_id,
            basis=basis,
            total=total,
            budget=budget or {},
            ledger=ledger if isinstance(ledger, dict) else {},
            by_task=task_rows,
            by_model=model_rows,
        )

    def artifact_content(self, run_id: str, sha256: str) -> ContentView | None:
        if not _SHA256.fullmatch(sha256):
            return None
        artifact = next(
            (item for item in self.list_artifacts_for_run(run_id) if item.sha256 == sha256), None
        )
        run_dir = self._run_dir(run_id)
        if artifact is None or run_dir is None:
            return None
        path = run_dir / "artifacts" / "blobs" / sha256
        if not path.is_file():
            return None
        return self._content_view(sha256, path, media_type=artifact.media_type, capture_level=None)

    def content(self, run_id: str, sha256: str) -> ContentView | None:
        if not _SHA256.fullmatch(sha256):
            return None
        refs = self._content_refs(run_id)
        ref = next((item for item in refs if item.get("sha256") == sha256), None)
        if ref is None:
            return None
        try:
            level = CaptureLevel(str(ref.get("capture_level") or "metadata"))
        except ValueError:
            level = CaptureLevel.METADATA
        if level in {CaptureLevel.OFF, CaptureLevel.METADATA}:
            return ContentView(
                sha256=sha256,
                available=False,
                capture_level=level,
                media_type=ref.get("media_type"),
                byte_count=ref.get("byte_count"),
            )
        run_dir = self._run_dir(run_id)
        if run_dir is None:
            return None
        path = run_dir / "content" / sha256
        if not path.is_file():
            return ContentView(
                sha256=sha256,
                available=False,
                capture_level=level,
                media_type=ref.get("media_type"),
                byte_count=ref.get("byte_count"),
            )
        return self._content_view(
            sha256,
            path,
            media_type=ref.get("media_type"),
            capture_level=level,
            byte_count=ref.get("byte_count"),
        )

    def _cost_row(self, labels: dict[str, Any], usages: list[dict[str, Any]]) -> dict[str, Any]:
        totals = _usage_totals(usages)
        totals.pop("reported_count", None)
        totals["estimated_cost_usd"] = str(totals["estimated_cost_usd"])
        totals["reported_cost_usd"] = str(totals["reported_cost_usd"])
        return {**labels, **totals, "invocation_count": len(usages)}

    def _run_dir(self, run_id: str) -> Path | None:
        if self.data_dir is None or not self.db.get_run(run_id):
            return None
        return self.data_dir / "runs" / run_id

    def _read_run_output(self, run_dir: Path, name: str) -> Any:
        if name not in _RUN_OUTPUT_FILES:
            return None
        return self._read_json(run_dir / "output" / name)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _content_refs(self, run_id: str) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        for event in self.db.list_events(run_id=run_id, limit=100_000):
            refs.extend(_json_loads(event.get("content_refs_json"), []))
        for invocation in self.db.list_invocations(run_id):
            refs.extend(_json_loads(invocation.get("content_refs_json"), []))
        return [ref for ref in refs if isinstance(ref, dict)]

    @staticmethod
    def _content_view(
        sha256: str,
        path: Path,
        *,
        media_type: str | None,
        capture_level: CaptureLevel | None,
        byte_count: Any = None,
    ) -> ContentView:
        raw = path.read_bytes()
        truncated = len(raw) > 1_000_000
        visible = raw[:1_000_000]
        text = visible.decode("utf-8", errors="replace")
        payload: Any = text
        if media_type == "application/json":
            with contextlib.suppress(json.JSONDecodeError):
                payload = json.loads(text)
        return ContentView(
            sha256=sha256,
            available=True,
            capture_level=capture_level or CaptureLevel.FULL,
            media_type=media_type,
            byte_count=byte_count or len(raw),
            truncated=truncated,
            payload=payload,
        )

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
