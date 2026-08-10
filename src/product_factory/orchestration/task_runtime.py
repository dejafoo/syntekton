"""TaskRuntimeService — broker construction, grants, and executor dispatch (SR2).

Owns ToolBroker construction, skill-grant enforcement, CapabilityGrant setup, and
``execute_task`` dispatch. Context packing and worktree lineage remain sequenced
by the lifecycle engine until a later cut.

Do not import the compatibility facade or grow policy/composition logic here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from product_factory.config.loader import AppConfig
from product_factory.connectors.broker import EVENT_INVOKED as CONNECTOR_EVENT_INVOKED
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.registry import ConnectorRegistry
from product_factory.connectors.source_ledger import SourceLedger
from product_factory.domain.tasks import TaskResult
from product_factory.domain.tools import CapabilityGrant, ToolCallRecord
from product_factory.executors import execute_task
from product_factory.gateway.base import ModelGateway
from product_factory.observability.contracts import EventSeverity
from product_factory.orchestration.implementation_helpers import deterministic_impl_files
from product_factory.orchestration.skill_grants import enforce_skill_grants
from product_factory.orchestration.task_contracts import TaskRuntimeRequest
from product_factory.orchestration.task_preparation import TaskPreparationService
from product_factory.orchestration.validation_repair.service import changed_files_from_patch
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.tools.broker import ToolBroker
from product_factory.tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class TaskRuntimeOutcome:
    """Executor result plus tool-call history for durable recording."""

    result: TaskResult
    tool_call_records: tuple[ToolCallRecord, ...]


class TaskRuntimeService:
    """Build ToolBroker, enforce grants, and dispatch via the executor registry."""

    def __init__(
        self,
        *,
        config: AppConfig,
        tool_registry: ToolRegistry,
        connector_broker: ConnectorBroker,
        connector_registry: ConnectorRegistry,
        task_preparation: TaskPreparationService,
        raw_gateway: ModelGateway,
        allow_deterministic_workers: bool,
        on_artifact_instance: Callable[[ArtifactInstance], None],
        approval_verify: Callable[..., bool],
    ) -> None:
        self.config = config
        self.tool_registry = tool_registry
        self.connector_broker = connector_broker
        self.connector_registry = connector_registry
        self.task_preparation = task_preparation
        self.raw_gateway = raw_gateway
        self.allow_deterministic_workers = allow_deterministic_workers
        self.on_artifact_instance = on_artifact_instance
        self.approval_verify = approval_verify

    def execute(
        self,
        runtime_request: TaskRuntimeRequest,
    ) -> TaskRuntimeOutcome:
        """Construct broker, enforce skill grants, set CapabilityGrant, execute."""

        prepared = runtime_request.prepared_task
        session = runtime_request.execution_session
        run_id = prepared.run_id
        run_dir = session.run_dir
        request = prepared.run_request
        task = prepared.task_spec
        effective_policy = prepared.effective_policy
        agent_profile = prepared.agent_profile
        model_profile = prepared.model_profile
        skills = list(prepared.matched_skills)
        artifacts = session.artifacts
        gateway = session.gateway
        ledger = session.ledger
        wt_path = prepared.workspace.root
        original_repo = prepared.workspace.original_repository
        base_commit = prepared.workspace.base_revision
        ctx_messages = prepared.prompt_package.messages
        package_hash = prepared.prompt_package.package_hash
        registered_command_ids = list(prepared.registered_command_ids)
        dependency_outputs = list(prepared.dependency_outputs)
        repository_excerpts = list(prepared.repository_excerpts)
        land_map = prepared.land_map
        composer_role = prepared.composition_role
        validation_evidence_refs = list(prepared.validation_evidence_refs)
        validator_results = list(prepared.validator_results)
        composition = prepared.composition
        recorder = session.recorder

        def _tool_observer(phase: str, payload: dict) -> None:
            if recorder is None:
                return
            severity = EventSeverity.ERROR if phase == "failed" else EventSeverity.INFO
            recorder.emit(
                run_id=run_id,
                event_type=f"tool.call.{phase}",
                task_id=task.id,
                tool_call_id=payload.get("tool_call_id"),
                summary=str(payload.get("tool_name") or phase),
                payload=payload,
                severity=severity,
            )

        def _connector_audit(event_type: str, payload: dict) -> None:
            if recorder is None:
                return
            severity = (
                EventSeverity.INFO if event_type == CONNECTOR_EVENT_INVOKED else EventSeverity.ERROR
            )
            recorder.emit(
                run_id=run_id,
                event_type=event_type,
                task_id=task.id,
                tool_call_id=payload.get("tool_call_id"),
                summary=f"{payload.get('connector_id') or '?'}:{payload.get('tool_name') or '?'}",
                payload=payload,
                severity=severity,
            )

        broker = ToolBroker(
            registry=self.tool_registry,
            artifact_store=artifacts,
            worktree_root=(
                wt_path
                if original_repo or effective_policy.executor_mode == "interface_agent_loop"
                else None
            ),
            original_repo=original_repo,
            registered_commands=self.config.policies.registered_commands,
            base_commit=base_commit or None,
            observer=_tool_observer if recorder is not None else None,
            ledger=ledger,
            connectors=self.connector_broker,
            connector_audit=_connector_audit,
            source_ledger=SourceLedger.for_run(run_dir),
            source_policy=resolve_request_source_policy(
                request, profiles_root=self.config.root / "profiles"
            ),
            # SR1: trust.approvals owns verification; runtime only wires the broker flag
            # (issue: remove-coordinator-approval-verify-2026-08).
            connector_approval_verified=self.approval_verify(
                request, consumer_run_id=run_id, capability=task.capability
            ),
            run_id=run_id,
            capture_level=recorder.capture_level if recorder is not None else None,
            on_artifact_instance=self.on_artifact_instance,
        )
        # The persisted effective policy is the sole authority for the broker.
        # Callers cannot provide an alternative or broaden this grant.
        effective_policy.ensure_valid_digest()
        granted = set(effective_policy.allowed_tool_names)

        # Fail closed before granting if a matched skill's declared tool policy
        # is inconsistent with the task's actual grant (P1.E).
        enforce_skill_grants(
            skills=skills,
            granted_tool_names=granted,
            connector_tools=self.connector_registry.tool_names_by_class(),
        )

        broker.set_grant(
            CapabilityGrant(
                grant_id=f"grant-{task.id}",
                run_id=run_id,
                task_id=task.id,
                agent_profile=agent_profile,
                tool_names=granted,
                allowed_path_patterns=task.allowed_path_patterns,
                readable_path_patterns=task.effective_read_patterns(),
                writable_path_patterns=task.effective_write_patterns(),
                # Reserve headroom for post-loop system git_diff / status calls.
                max_calls=int(
                    effective_policy.call_limits.get(
                        "max_calls",
                        max(task.budget.max_tool_calls * 2, task.budget.max_tool_calls + 10),
                    )
                ),
            )
        )

        # SD1: dispatch declared work through the executor registry.
        # SD1 temporary: composition still receives coordinator compose callbacks
        # (issue: remove-coordinator-compose-callbacks-2026-08).
        # SR2: TaskPreparationService owns request assembly.
        execution_request = self.task_preparation.assemble_execution_request(
            run_id=run_id,
            run_dir=run_dir,
            request=request,
            task=task,
            effective_policy=effective_policy,
            agent_profile=agent_profile,
            model_profile=model_profile,
            broker=broker,
            artifacts=artifacts,
            gateway=gateway,
            raw_gateway=self.raw_gateway,
            allow_deterministic_workers=self.allow_deterministic_workers,
            ctx_messages=ctx_messages,
            package_hash=package_hash,
            granted_tool_names=granted,
            registered_command_ids=list(registered_command_ids or []),
            dependency_outputs=dependency_outputs or [],
            repository_excerpts=repository_excerpts or [],
            base_commit=base_commit,
            land_map=land_map,
            composer_role=composer_role,
            validation_evidence_refs=validation_evidence_refs or [],
            validator_results=validator_results or [],
            composition=composition,
            deterministic_implementation=deterministic_impl_files,
            patch_changed_files=changed_files_from_patch,
        )
        result = execute_task(execution_request)
        return TaskRuntimeOutcome(
            result=result,
            tool_call_records=tuple(broker.history),
        )
