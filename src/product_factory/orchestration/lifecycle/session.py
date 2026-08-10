"""Per-run execution-session construction."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from product_factory.domain.runs import RunRequest
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.observability.events import EventLog
from product_factory.observability.otel import maybe_create_otel_bridge
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.persistence.artifacts import ArtifactStore
from product_factory.persistence.database import Database
from product_factory.workflows.base import WorkflowPack


class RunExecutionSessionFactory:
    """Construct isolated run-scoped telemetry, gateway, budget, and stores."""

    def __init__(
        self,
        *,
        database: Database,
        raw_gateway: ModelGateway,
        cancel_check: Callable[[str], None],
    ) -> None:
        self._database = database
        self._raw_gateway = raw_gateway
        self._cancel_check = cancel_check

    def open(
        self,
        *,
        run_id: str,
        request: RunRequest,
        run_dir: Path,
        budget_snapshot: dict[str, object] | None = None,
        workflow_pack: WorkflowPack | None = None,
    ) -> RunExecutionContext:
        events = EventLog(run_dir / "events.jsonl")
        artifacts = ArtifactStore(run_dir / "artifacts")
        recorder = TelemetryRecorder(
            self._database,
            jsonl=events,
            content_dir=run_dir / "content",
            otel_exporter=maybe_create_otel_bridge(),
        )
        ledger = (
            BudgetLedger.restore(request.budget, budget_snapshot)
            if budget_snapshot
            else BudgetLedger(request.budget)
        )
        gateway = InstrumentedModelGateway(
            self._raw_gateway,
            recorder=recorder,
            db=self._database,
            ledger=ledger,
        )
        workspace_key = (
            str(request.repository_path.resolve())
            if request.repository_path is not None
            else (
                f"{request.workspace.repository_id}:{request.workspace.ref}"
                if request.workspace is not None
                else request.repository_id
            )
        )
        return RunExecutionContext(
            run_id=run_id,
            workflow_type=request.workflow_type,
            run_dir=run_dir,
            gateway=gateway,
            recorder=recorder,
            ledger=ledger,
            artifacts=artifacts,
            events=events,
            cancel_check=lambda: self._cancel_check(run_id),
            pack_id=workflow_pack.id if workflow_pack else None,
            pack_version=workflow_pack.version if workflow_pack else None,
            workspace_key=workspace_key,
        )
