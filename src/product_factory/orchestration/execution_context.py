"""Immutable run-scoped execution dependencies.

Long-lived coordinators may serve several workers concurrently.  Anything that
attributes model/tool work, spends a budget, or writes run content therefore
belongs here rather than on ``RunCoordinator`` itself.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from product_factory.gateway.instrumented import InstrumentedModelGateway
from product_factory.observability.events import EventLog
from product_factory.observability.recorder import TelemetryRecorder
from product_factory.orchestration.budget_ledger import BudgetLedger
from product_factory.persistence.artifacts import ArtifactStore

if TYPE_CHECKING:
    from product_factory.workflows.base import WorkflowPack


@dataclass(frozen=True, slots=True)
class RunExecutionContext:
    """Dependencies whose identity must never cross a run boundary."""

    run_id: str
    workflow_type: str
    run_dir: Path
    gateway: InstrumentedModelGateway
    recorder: TelemetryRecorder
    ledger: BudgetLedger
    artifacts: ArtifactStore
    events: EventLog
    cancel_check: Callable[[], None]
    pack_id: str | None = None
    pack_version: str | None = None
    workspace_key: str | None = None
    route_policy_ref: str | None = None

    def with_pack(self, pack: WorkflowPack | None) -> RunExecutionContext:
        """Return a context carrying the resolved immutable pack identity."""
        if pack is None:
            return self
        return RunExecutionContext(
            run_id=self.run_id,
            workflow_type=self.workflow_type,
            run_dir=self.run_dir,
            gateway=self.gateway,
            recorder=self.recorder,
            ledger=self.ledger,
            artifacts=self.artifacts,
            events=self.events,
            cancel_check=self.cancel_check,
            pack_id=pack.id,
            pack_version=pack.version,
            workspace_key=self.workspace_key,
            route_policy_ref=self.route_policy_ref,
        )
