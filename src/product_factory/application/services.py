"""Application-level query, worker, shutdown, and metadata services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from product_factory.application.command_service import LifecycleCommandService
from product_factory.config.loader import AppConfig
from product_factory.domain.errors import ProductFactoryError, RunCancelledError
from product_factory.domain.runs import RunManifest, RunRequest
from product_factory.observability.query import ObservabilityQueryService
from product_factory.workers.supervisor import ShutdownReport, WorkerSupervisor

if TYPE_CHECKING:
    from product_factory.gateway.base import ModelGateway
    from product_factory.persistence.database import Database


@dataclass(frozen=True, slots=True)
class ApplicationMetadata:
    """Non-authoritative construction metadata for adapters and diagnostics."""

    data_root: Path
    configuration_root: Path
    deterministic_planner: bool
    deterministic_workers: bool


class ApplicationQueryService:
    """Read boundary over durable projections.

    ``database`` is temporarily exposed for legacy host read helpers. R4 removes
    host-owned writes and narrows this surface to aggregate query methods.
    """

    def __init__(self, database: Database, *, data_root: Path, config: AppConfig) -> None:
        self._database = database
        self.data_root = data_root
        self.config = config
        self.observability = ObservabilityQueryService(database, data_dir=data_root)

    @property
    def database(self) -> Database:
        return self._database


class RunWorkerBackend:
    """Application-owned execution backend used by the durable supervisor."""

    def __init__(
        self,
        *,
        config: AppConfig,
        gateway: ModelGateway,
        data_root: Path,
        database: Database,
        commands: LifecycleCommandService,
    ) -> None:
        self.config = config
        self.gateway = gateway
        self.data_root = data_root
        self.database = database
        self.commands = commands

    def execute(self, run_id: str) -> RunManifest:
        request_path = self.data_root / "runs" / run_id / "input" / "request.json"
        if not request_path.exists():
            raise ProductFactoryError(f"No submitted request for {run_id}")
        request = RunRequest.model_validate_json(request_path.read_text(encoding="utf-8"))
        row = self.database.get_run(run_id)
        if self.database.is_cancel_requested(run_id):
            self.database.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="cancelled",
                request=request.model_dump(mode="json"),
                base_commit=row.get("base_commit") if row else None,
                usage=json.loads(row["usage_json"]) if row and row.get("usage_json") else {},
                active_operation=None,
            )
            raise RunCancelledError(f"Run {run_id} cancelled by operator")
        if row and row["status"] == "queued":
            self.database.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="initializing",
                request=request.model_dump(mode="json"),
                active_operation="initializing",
            )
        try:
            return self.commands.submit(request, run_id=run_id)
        except RunCancelledError:
            current = self.database.get_run(run_id)
            self.database.upsert_run(
                run_id=run_id,
                workflow_type=request.workflow_type,
                status="cancelled",
                request=request.model_dump(mode="json"),
                base_commit=current.get("base_commit") if current else None,
                usage=(
                    json.loads(current["usage_json"])
                    if current and current.get("usage_json")
                    else {}
                ),
                active_operation=None,
            )
            raise

    def worktree_key(self, run_id: str) -> str:
        return str((self.data_root / "runs" / run_id / "worktrees").resolve())

    def on_error(self, run_id: str, exc: Exception, recovery: bool) -> None:
        if isinstance(exc, RunCancelledError):
            return
        row = self.database.get_run(run_id)
        if row and row["status"] == "cancelled":
            return
        request_path = self.data_root / "runs" / run_id / "input" / "request.json"
        request: dict[str, Any] = {}
        if request_path.exists():
            request = json.loads(request_path.read_text(encoding="utf-8"))
        self.database.upsert_run(
            run_id=run_id,
            workflow_type=str(request.get("workflow_type") or "code_change"),
            status="failed",
            request=request,
            active_operation="recovery_failed" if recovery else "failed",
        )
        path = self.data_root / "runs" / run_id / "output" / "host_worker_error.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "error": exc.__class__.__name__,
                    "message": str(exc),
                    "recoverable": recovery,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


class RunWorkerService:
    """Typed facade over one application-owned worker supervisor."""

    def __init__(self, *, backend: RunWorkerBackend, supervisor: WorkerSupervisor) -> None:
        self.backend = backend
        self.supervisor = supervisor

    @property
    def running(self) -> bool:
        return self.supervisor.running

    def start(self) -> None:
        self.supervisor.start()

    def spawn(self, run_id: str, *, recovery: bool = False) -> bool:
        return self.supervisor.spawn(run_id, recovery=recovery)

    def wait(self, run_id: str, *, timeout: float | None = None) -> bool:
        return self.supervisor.wait(run_id, timeout=timeout)

    def run_blocking(self, run_id: str, *, recovery: bool = False) -> RunManifest:
        return self.supervisor.run_blocking(run_id, recovery=recovery)

    def execute(self, run_id: str) -> RunManifest:
        return self.backend.execute(run_id)

    def drain(self, *, grace_seconds: float | None = None) -> ShutdownReport:
        return self.supervisor.drain(grace_seconds=grace_seconds, close_database=False)


class ApplicationShutdownService:
    """Own graceful worker drain followed by database shutdown."""

    def __init__(self, *, workers: RunWorkerService, database: Database) -> None:
        self._workers = workers
        self._database = database
        self._closed = False

    def close(self, *, grace_seconds: float | None = None) -> ShutdownReport:
        if self._closed:
            return ShutdownReport(database_closed=True)
        report = self._workers.drain(grace_seconds=grace_seconds)
        self._database.close()
        report.database_closed = True
        self._closed = True
        return report
