"""Shared host service used by CLI (P3.A), HTTP (P3.B), and MCP (P3.C)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from product_factory.config.loader import AppConfig
from product_factory.domain.errors import ProductFactoryError, RunCancelledError
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.mock import MockGateway
from product_factory.host.export import export_evidence_bundle
from product_factory.host.protocol import HostResponse, HostSubscription
from product_factory.observability.query import ObservabilityQueryService
from product_factory.orchestration.coordinator import RunCoordinator

DEFAULT_OBSERVE_URL = "http://127.0.0.1:8765"
TERMINAL_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "blocked",
        "budget_exhausted",
        "plan_rejected",
        "cancelled",
    }
)


class HostService:
    """Orchestration facade for machine hosts (no duplicated coordinator logic)."""

    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        data_dir: Path | None = None,
        use_deterministic_planner: bool = False,
        observe_base_url: str | None = None,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.data_dir = data_dir
        self.use_deterministic_planner = use_deterministic_planner or isinstance(
            gateway, MockGateway
        )
        self.observe_base_url = (
            observe_base_url
            or os.environ.get("PRODUCT_FACTORY_OBSERVE_URL")
            or DEFAULT_OBSERVE_URL
        ).rstrip("/")
        self.coord = RunCoordinator(
            config=config,
            gateway=gateway,
            data_dir=data_dir,
            use_deterministic_planner=self.use_deterministic_planner,
        )
        self.pf_root = self.coord.pf_root
        self.query = ObservabilityQueryService(self.coord.db, data_dir=self.pf_root)

    def subscription_for(self, run_id: str, *, after_seq: int = 0) -> HostSubscription:
        return HostSubscription(
            sse_url=(
                f"{self.observe_base_url}/api/v1/runs/{run_id}/events/stream"
                f"?after_seq={after_seq}"
            ),
            cli_tail=f"product-factory host tail {run_id}",
        )

    def submit(
        self,
        request: RunRequest,
        *,
        mock: bool = False,
        detach: bool = True,
        inline_thread: bool = False,
    ) -> HostResponse:
        """Queue a run and start a background worker; return immediately."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        run_dir = self.pf_root / "runs" / run_id
        for sub in (
            "input",
            "worktrees",
            "scratch",
            "artifacts",
            "findings",
            "prompts",
            "output",
            "content",
        ):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)

        (run_dir / "input" / "request.md").write_text(request.request_text, encoding="utf-8")
        (run_dir / "input" / "request.json").write_text(
            request.model_dump_json(indent=2), encoding="utf-8"
        )
        worker_opts = {
            "mock": mock or isinstance(self.gateway, MockGateway),
            "use_deterministic_planner": self.use_deterministic_planner,
        }
        (run_dir / "input" / "host_worker.json").write_text(
            json.dumps(worker_opts, indent=2) + "\n", encoding="utf-8"
        )

        self.coord.db.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status="queued",
            request=request.model_dump(mode="json"),
            active_operation="queued",
        )

        if inline_thread:
            thread = threading.Thread(
                target=self._safe_worker,
                kwargs={"run_id": run_id},
                name=f"pf-host-worker-{run_id}",
                daemon=True,
            )
            thread.start()
        elif detach:
            self._spawn_worker(run_id, mock=bool(worker_opts["mock"]))
        else:
            # Synchronous execute (tests / debugging only).
            self.run_worker(run_id)

        return HostResponse.success(
            run_id=run_id,
            status="queued",
            subscription=self.subscription_for(run_id),
            data={"request_id": request.request_id, "workflow_type": request.workflow_type},
        )

    def _safe_worker(self, *, run_id: str) -> None:
        try:
            self.run_worker(run_id)
        except RunCancelledError:
            # Coordinator already persisted typed `cancelled` status.
            return
        except Exception as exc:  # noqa: BLE001 — persist failure for hosts polling status
            row = self.coord.db.get_run(run_id)
            if row and row["status"] == "cancelled":
                return
            self.coord.db.upsert_run(
                run_id=run_id,
                workflow_type=self._workflow_type(run_id) or "code_change",
                status="failed",
                request=self._request_dict(run_id) or {},
                active_operation="failed",
            )
            fail_path = self.pf_root / "runs" / run_id / "output" / "host_worker_error.json"
            fail_path.parent.mkdir(parents=True, exist_ok=True)
            fail_path.write_text(
                json.dumps(
                    {
                        "error": exc.__class__.__name__,
                        "message": str(exc),
                        "at": datetime.now(UTC).isoformat(),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    def _spawn_worker(self, run_id: str, *, mock: bool) -> None:
        cmd = [
            sys.executable,
            "-m",
            "product_factory",
            "host",
            "worker",
            "--run-id",
            run_id,
        ]
        if mock:
            cmd.append("--mock")
        if self.data_dir is not None:
            cmd.extend(["--data-dir", str(self.data_dir)])
        env = os.environ.copy()
        if mock:
            env["PRODUCT_FACTORY_FORCE_MOCK"] = "1"
        log_path = self.pf_root / "runs" / run_id / "scratch" / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("a", encoding="utf-8")
        subprocess.Popen(  # noqa: S603 — fixed argv, local worker only
            cmd,
            cwd=str(self.config.root),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def run_worker(self, run_id: str) -> RunManifest:
        """Execute a previously submitted run (blocking)."""
        run_dir = self.pf_root / "runs" / run_id
        request_path = run_dir / "input" / "request.json"
        if not request_path.exists():
            raise ProductFactoryError(f"No submitted request for {run_id}")
        if self.coord.db.is_cancel_requested(run_id):
            row = self.coord.db.get_run(run_id)
            request = RunRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
            self.coord.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="cancelled",
                request=request.model_dump(mode="json"),
                base_commit=row.get("base_commit") if row else None,
                usage=json.loads(row["usage_json"]) if row and row.get("usage_json") else {},
                active_operation=None,
            )
            raise RunCancelledError(f"Run {run_id} cancelled by operator")
        request = RunRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
        row = self.coord.db.get_run(run_id)
        if row and row["status"] == "queued":
            self.coord.db.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="initializing",
                request=request.model_dump(mode="json"),
                active_operation="initializing",
            )
        return self.coord.run(request, run_id=run_id)

    def status(self, run_id: str) -> HostResponse:
        row = self.coord.db.get_run(run_id)
        if not row:
            return HostResponse.failure(
                code="not_found", message=f"Unknown run {run_id}", run_id=run_id
            )
        summary = self.query.get_run(run_id)
        plan_summary = self._plan_summary(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"],
            plan_summary=plan_summary,
            subscription=self.subscription_for(run_id),
            data={
                "workflow_type": row["workflow_type"],
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
                "usage": json.loads(row.get("usage_json") or "{}"),
                "liveness": summary.liveness.value if summary else None,
                "latest_seq": summary.latest_seq if summary else 0,
                "task_counts": summary.task_counts if summary else {},
            },
        )

    def inspect(self, run_id: str) -> HostResponse:
        row = self.coord.db.get_run(run_id)
        if not row:
            return HostResponse.failure(
                code="not_found", message=f"Unknown run {run_id}", run_id=run_id
            )
        run_dir = self.pf_root / "runs" / run_id
        manifest_path = run_dir / "run-manifest.json"
        manifest: dict[str, Any] | None = None
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan = self.query.plan(run_id)
        validations = self.coord.db.list_validator_results(run_id)
        artifacts = self._artifact_dicts(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"],
            plan_summary=self._plan_summary(run_id),
            artifacts=artifacts,
            data={
                "manifest": manifest,
                "plan": plan.model_dump(mode="json") if plan else None,
                "validations": validations,
                "approval": self._approval_record(run_id),
            },
        )

    def artifacts(self, run_id: str) -> HostResponse:
        row = self.coord.db.get_run(run_id)
        if not row:
            return HostResponse.failure(
                code="not_found", message=f"Unknown run {run_id}", run_id=run_id
            )
        return HostResponse.success(
            run_id=run_id,
            status=row["status"],
            artifacts=self._artifact_dicts(run_id),
        )

    def approve(self, run_id: str, *, apply: bool = False) -> HostResponse:
        try:
            result = self.coord.approve(run_id, apply=apply)
        except ProductFactoryError as exc:
            return HostResponse.failure(
                code=exc.__class__.__name__,
                message=exc.message,
                run_id=run_id,
                details=exc.details,
            )
        row = self.coord.db.get_run(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"] if row else "completed",
            data={"approval": result},
        )

    def reject(self, run_id: str) -> HostResponse:
        try:
            result = self.coord.reject(run_id)
        except ProductFactoryError as exc:
            return HostResponse.failure(
                code=exc.__class__.__name__,
                message=exc.message,
                run_id=run_id,
                details=exc.details,
            )
        row = self.coord.db.get_run(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"] if row else "blocked",
            data={"approval": result},
        )

    def cancel(self, run_id: str) -> HostResponse:
        try:
            result = self.coord.cancel(run_id)
        except ProductFactoryError as exc:
            return HostResponse.failure(
                code=exc.__class__.__name__,
                message=exc.message,
                run_id=run_id,
                details=exc.details,
            )
        row = self.coord.db.get_run(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"] if row else result.get("status"),
            data=result,
        )

    def revise(self, run_id: str, *, note: str = "") -> HostResponse:
        try:
            manifest = self.coord.revise(run_id, note=note)
        except ProductFactoryError as exc:
            row = self.coord.db.get_run(run_id)
            return HostResponse.failure(
                code=exc.__class__.__name__,
                message=exc.message,
                run_id=run_id,
                status=row["status"] if row else None,
                details=exc.details,
            )
        row = self.coord.db.get_run(run_id)
        return HostResponse.success(
            run_id=run_id,
            status=row["status"] if row else manifest.final_status,
            plan_summary=self._plan_summary(run_id),
            data={
                "revision_note": note,
                "manifest_status": manifest.final_status,
                "grants_unchanged": True,
            },
        )

    def export_bundle(self, run_id: str, *, as_zip: bool = True) -> HostResponse:
        row = self.coord.db.get_run(run_id)
        if not row:
            return HostResponse.failure(
                code="not_found", message=f"Unknown run {run_id}", run_id=run_id
            )
        try:
            result = export_evidence_bundle(
                pf_root=self.pf_root,
                db=self.coord.db,
                run_id=run_id,
                as_zip=as_zip,
            )
        except FileNotFoundError as exc:
            return HostResponse.failure(
                code="not_found", message=str(exc), run_id=run_id
            )
        except OSError as exc:
            return HostResponse.failure(
                code="export_failed",
                message=str(exc),
                run_id=run_id,
            )
        return HostResponse.success(
            run_id=run_id,
            status=row["status"],
            artifacts=self._artifact_dicts(run_id),
            data=result,
        )

    def plan_preview(
        self,
        request_text: str,
        *,
        workflow_type: str = "code_change",
    ) -> HostResponse:
        """Compile a plan without creating a run or executing workers."""
        from product_factory.orchestration.coordinator import (
            default_code_change_plan,
            default_investigation_plan,
            default_technical_plan,
        )
        from product_factory.planning.compiler import compile_plan

        if workflow_type in {"architecture", "technical_plan"}:
            proposal = default_technical_plan(request_text)
        elif workflow_type == "repository_investigation":
            proposal = default_investigation_plan(request_text)
        else:
            proposal = default_code_change_plan(request_text)
        result = compile_plan(proposal)
        plan = result.plan
        plan_summary = {
            "objective": proposal.objective,
            "task_count": len(plan.tasks) if plan else 0,
            "task_ids": list(plan.task_order) if plan else [],
        }
        payload = {
            "workflow_type": workflow_type,
            "compiler": result.model_dump(mode="json"),
        }
        if result.ok:
            return HostResponse.success(plan_summary=plan_summary, data=payload)
        return HostResponse.failure(
            code="plan_rejected",
            message="Plan compilation failed",
            details=payload,
        )

    def list_events(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Prefer SQLite; fall back to events.jsonl with synthetic seq."""
        events = self.query.list_events(run_id=run_id, after_seq=after_seq, limit=limit)
        if events:
            return events
        return self._events_from_jsonl(run_id, after_seq=after_seq, limit=limit)

    def tail(
        self,
        run_id: str,
        *,
        after_seq: int = 0,
        follow: bool = True,
        idle_seconds: float = 0.35,
        max_idle_polls: int | None = None,
        stop_when_terminal: bool = True,
    ) -> Iterator[HostResponse]:
        """Yield HostResponse batches. Tries observe HTTP, then local SQLite/jsonl."""
        cursor = after_seq
        idle_polls = 0
        while True:
            batch, source = self._fetch_event_batch(run_id, after_seq=cursor, limit=100)
            row = self.coord.db.get_run(run_id)
            status = row["status"] if row else None
            if batch:
                idle_polls = 0
                cursor = int(batch[-1].get("seq") or cursor)
                yield HostResponse.success(
                    run_id=run_id,
                    status=status,
                    events=batch,
                    subscription=self.subscription_for(run_id, after_seq=cursor),
                    data={"source": source, "after_seq": cursor},
                )
            else:
                idle_polls += 1
                yield HostResponse.success(
                    run_id=run_id,
                    status=status,
                    events=[],
                    subscription=self.subscription_for(run_id, after_seq=cursor),
                    data={
                        "source": source,
                        "after_seq": cursor,
                        "heartbeat": True,
                        "idle_polls": idle_polls,
                    },
                )
                if stop_when_terminal and status in TERMINAL_STATUSES | {"awaiting_approval"}:
                    return
                if not follow:
                    return
                if max_idle_polls is not None and idle_polls >= max_idle_polls:
                    return
                time.sleep(idle_seconds)

    def _fetch_event_batch(
        self, run_id: str, *, after_seq: int, limit: int
    ) -> tuple[list[dict[str, Any]], str]:
        remote = self._try_observe_events(run_id, after_seq=after_seq, limit=limit)
        if remote is not None:
            return remote, "observe"
        local = self.query.list_events(run_id=run_id, after_seq=after_seq, limit=limit)
        if local:
            return local, "sqlite"
        return self._events_from_jsonl(run_id, after_seq=after_seq, limit=limit), "jsonl"

    def _try_observe_events(
        self, run_id: str, *, after_seq: int, limit: int
    ) -> list[dict[str, Any]] | None:
        url = f"{self.observe_base_url}/api/v1/runs/{run_id}/events"
        try:
            with httpx.Client(timeout=0.75) as client:
                response = client.get(
                    url, params={"after_seq": after_seq, "limit": limit}
                )
                if response.status_code != 200:
                    return None
                payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError, OSError):
            return None
        if isinstance(payload, dict) and "items" in payload:
            items = payload["items"]
            return items if isinstance(items, list) else None
        if isinstance(payload, list):
            return payload
        return None

    def _events_from_jsonl(
        self, run_id: str, *, after_seq: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        path = self.pf_root / "runs" / run_id / "events.jsonl"
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for index, line in enumerate(fh, start=1):
                if index <= after_seq or not line.strip():
                    continue
                raw = json.loads(line)
                events.append(
                    {
                        "seq": index,
                        "event_id": raw.get("event_id") or f"jsonl-{index}",
                        "type": raw.get("type") or raw.get("event_type") or "event",
                        "run_id": raw.get("run_id") or run_id,
                        "occurred_at": raw.get("timestamp") or raw.get("occurred_at"),
                        "summary": raw.get("summary") or "",
                        "payload": raw.get("payload") or {},
                    }
                )
                if len(events) >= limit:
                    break
        return events

    def _plan_summary(self, run_id: str) -> dict[str, Any] | None:
        plan_path = self.pf_root / "runs" / run_id / "output" / "plan.json"
        if not plan_path.exists():
            return None
        try:
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        tasks = plan.get("tasks") or []
        return {
            "objective": plan.get("objective"),
            "task_count": len(tasks) if isinstance(tasks, list) else 0,
            "task_ids": [
                t.get("id") for t in tasks if isinstance(t, dict) and t.get("id")
            ]
            if isinstance(tasks, list)
            else [],
        }

    def _artifact_dicts(self, run_id: str) -> list[dict[str, Any]]:
        views = self.query.list_artifacts_for_run(run_id)
        out = [v.model_dump(mode="json") for v in views]
        # Also surface well-known output files even before DB artifact rows.
        run_dir = self.pf_root / "runs" / run_id / "output"
        candidates = [
            run_dir / "plan.json",
            run_dir / "proposed.patch",
            run_dir / "architecture.md",
            run_dir / "approval.json",
            self.pf_root / "runs" / run_id / "run-manifest.json",
        ]
        for path in candidates:
            name = path.name
            if path.is_file() and not any(a.get("logical_name") == name for a in out):
                out.append(
                    {
                        "logical_name": name,
                        "relative_path": str(path.relative_to(self.pf_root)),
                        "size_bytes": path.stat().st_size,
                        "trust_level": "generated",
                    }
                )
        return out

    def _approval_record(self, run_id: str) -> dict[str, Any] | None:
        path = self.pf_root / "runs" / run_id / "output" / "approval.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _workflow_type(self, run_id: str) -> str | None:
        row = self.coord.db.get_run(run_id)
        return row["workflow_type"] if row else None

    def _request_dict(self, run_id: str) -> dict[str, Any] | None:
        row = self.coord.db.get_run(run_id)
        if not row:
            return None
        try:
            return json.loads(row["request_json"])
        except json.JSONDecodeError:
            return None
