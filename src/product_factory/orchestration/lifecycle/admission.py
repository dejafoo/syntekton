"""Authoritative run admission and immutable input materialization."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from product_factory.config.loader import AppConfig
from product_factory.connectors.source_ledger import SourceLedger
from product_factory.domain.errors import ConfigurationError
from product_factory.domain.runs import RunRequest
from product_factory.observability.contracts import EventSeverity
from product_factory.orchestration.budget_ledger import warn_unused_profile_set
from product_factory.orchestration.execution_context import RunExecutionContext
from product_factory.orchestration.lifecycle.models import AdmittedRepository, RunSnapshot
from product_factory.persistence.database import Database
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.repositories.snapshot import snapshot_repository
from product_factory.repositories.worktrees import WorktreeManager
from product_factory.workflows.artifacts import ArtifactLandMap
from product_factory.workflows.handoffs import validate_pack_handoffs
from product_factory.workflows.inputs import persist_pack_input, validate_pack_input
from product_factory.workflows.registry import (
    is_registered_workflow,
    land_map_for_request,
    resolve_workflow_pack,
)

logger = logging.getLogger("product_factory.orchestration.admission")


class RunAdmissionService:
    """Resolve trusted admission state before planner or executor spend."""

    def __init__(self, *, config: AppConfig, database: Database, data_root: Path) -> None:
        self._config = config
        self._database = database
        self._data_root = data_root

    def admit(
        self,
        *,
        run_id: str,
        request: RunRequest,
        session: RunExecutionContext,
    ) -> tuple[RunSnapshot, RunExecutionContext]:
        run_dir = session.run_dir
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

        note = warn_unused_profile_set(request.model_profile_set)
        if note:
            logger.warning(note)
            session.recorder.emit(
                run_id=run_id,
                event_type="run.deprecation_warning",
                severity=EventSeverity.WARNING,
                summary=note,
                payload={"field": "model_profile_set", "value": request.model_profile_set},
            )
        session.recorder.emit(
            run_id=run_id,
            event_type="run.started",
            summary="Run started",
            payload={"workflow": request.workflow_type},
        )
        session.cancel_check()
        self._database.upsert_run(
            run_id=run_id,
            workflow_type=request.workflow_type,
            status="initializing",
            request=request.model_dump(mode="json"),
            active_operation="initializing",
        )
        (run_dir / "input" / "request.md").write_text(request.request_text, encoding="utf-8")
        (run_dir / "input" / "request.json").write_text(
            request.model_dump_json(indent=2), encoding="utf-8"
        )
        persist_pack_input(request.pack_input, run_dir / "input")

        workflow_pack = None
        land_map = ArtifactLandMap()
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            validate_pack_handoffs(request, workflow_pack)
            if request.handoff_refs:
                from product_factory.trust.handoffs import HandoffService

                resolved = HandoffService(self._database, self._data_root).resolve_refs(
                    request,
                    workflow_pack,
                    consumer_run_id=run_id,
                    materialize_dir=run_dir / "input",
                )
                (run_dir / "input" / "resolved-handoffs.json").write_text(
                    json.dumps(
                        [item.model_dump(mode="json") for item in resolved],
                        indent=2,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            validate_pack_input(workflow_pack, request.pack_input)
            land_map = land_map_for_request(request)
            session.recorder.emit(
                run_id=run_id,
                event_type="workflow.pack_resolved",
                summary=f"Workflow pack {workflow_pack.id}@{workflow_pack.version}",
                payload={
                    **workflow_pack.manifest_metadata(),
                    "artifact_land_map": land_map.as_payload(),
                },
            )
        session = session.with_pack(workflow_pack)

        source_policy = resolve_request_source_policy(
            request, profiles_root=self._config.root / "profiles"
        )
        SourceLedger.for_run(run_dir).record_seed_urls(
            request.pack_input.get("seed_source_urls") or (),
            policy=source_policy,
            task_id="run-input",
        )

        repository = AdmittedRepository(None, None, None, None)
        if request.repository_path is not None:
            snap = snapshot_repository(
                request.repository_path,
                allow_dirty=self._config.policies.allow_dirty_repo,
                output_dir=run_dir / "input",
            )
            if (
                request.workspace_provenance is not None
                and snap.base_commit != request.workspace_provenance.commit
            ):
                raise ConfigurationError(
                    "Prepared workspace revision changed before execution",
                    details={
                        "expected_commit": request.workspace_provenance.commit,
                        "actual_commit": snap.base_commit,
                    },
                )
            repository = AdmittedRepository(
                snap.base_commit,
                snap.manifest,
                snap.repository_path,
                WorktreeManager(snap.repository_path, run_dir / "worktrees"),
            )
            session.recorder.emit(
                run_id=run_id,
                event_type="repository.snapshot",
                summary="Repository snapshot",
                payload={"base_commit": snap.base_commit},
            )

        snapshot = RunSnapshot(
            run_id=run_id,
            request=request,
            status="initializing",
            run_dir=run_dir,
            workflow_pack=workflow_pack,
            land_map=land_map,
            repository=repository,
        )
        return snapshot, session

    def resume(
        self,
        *,
        run_id: str,
        request: RunRequest,
        status: str,
        base_commit: str | None,
        session: RunExecutionContext,
    ) -> tuple[RunSnapshot, RunExecutionContext]:
        """Re-resolve trusted pack and handoff state before resuming."""

        workflow_pack = None
        land_map = ArtifactLandMap()
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            validate_pack_handoffs(request, workflow_pack)
            if request.handoff_refs:
                from product_factory.trust.handoffs import HandoffService

                HandoffService(self._database, self._data_root).resolve_refs(
                    request,
                    workflow_pack,
                    consumer_run_id=run_id,
                    materialize_dir=session.run_dir / "input",
                )
            validate_pack_input(workflow_pack, request.pack_input)
            land_map = land_map_for_request(request)
        session = session.with_pack(workflow_pack)
        source_policy = resolve_request_source_policy(
            request, profiles_root=self._config.root / "profiles"
        )
        SourceLedger.for_run(session.run_dir).record_seed_urls(
            request.pack_input.get("seed_source_urls") or (),
            policy=source_policy,
            task_id="run-input",
        )
        original = request.repository_path.resolve() if request.repository_path else None
        repository = AdmittedRepository(
            base_commit,
            None,
            original,
            WorktreeManager(original, session.run_dir / "worktrees") if original else None,
        )
        return (
            RunSnapshot(
                run_id=run_id,
                request=request,
                status=status,
                run_dir=session.run_dir,
                workflow_pack=workflow_pack,
                land_map=land_map,
                repository=repository,
            ),
            session,
        )
