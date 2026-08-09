"""TaskPreparationService — effective policy and execution-request assembly (SR2).

Owns capability/executor resolution inputs, effective-policy calculation, and
immutable ``TaskExecutionRequest`` construction. Does not execute tasks.

The first extraction cut moves policy resolution and request assembly out of
``RunLifecycleEngine._execute_task``. Context packaging, worktree lineage, and
broker construction remain sequenced by the lifecycle engine until later cuts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from product_factory.config.loader import AppConfig
from product_factory.connectors.tavily import TOOL_WEB_SEARCH
from product_factory.domain.findings import ValidatorResult
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskResult, TaskSpec
from product_factory.executors.protocol import TaskExecutionRequest
from product_factory.executors.research_agent import (
    EVIDENCE_BUILD_TOOL_NAMES,
    SOURCE_READ_TOOL_NAMES,
)
from product_factory.orchestration.effective_policy import (
    EffectiveTaskPolicy,
    compute_allowed_tool_names,
    grantable_connector_names_for_task,
    resolve_effective_task_policy,
)
from product_factory.orchestration.validation_repair.service import (
    resolve_validation_command_ids,
)
from product_factory.policy.composition_gates import evaluate_composition_gates
from product_factory.policy.domain_packs import resolve_request_domain_packs
from product_factory.policy.policy_profiles import resolve_request_policy_profiles
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    agent_profile_for,
    require_descriptor,
)
from product_factory.scheduling.scheduler import resolve_task_model_profile
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.registry import ToolRegistry
from product_factory.workflows.registry import is_registered_workflow, resolve_workflow_pack

_RESEARCH_LOOP_CAPABILITIES = frozenset(
    capability_id
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
    if descriptor.executor_mode == "research_agent_loop"
)


@dataclass(frozen=True, slots=True)
class PreparedPolicy:
    """Successful effective-policy resolution for one task."""

    effective_policy: EffectiveTaskPolicy
    profile: str
    agent_profile: str
    skills: list[Any]
    skills_disabled: bool
    profile_digests: dict[str, str]
    pack_id: str | None
    pack_version: str | None
    reused_existing: bool


@dataclass(frozen=True, slots=True)
class BlockedPreparation:
    """Honest terminal prep outcome (composition conflict, etc.)."""

    result: TaskResult
    profile: str
    agent_profile: str
    skills: list[Any]
    skills_disabled: bool


class TaskPreparationService:
    """Resolve effective policy and assemble TaskExecutionRequest (no execute)."""

    def __init__(
        self,
        *,
        config: AppConfig,
        skills: SkillRegistry,
        tool_registry: ToolRegistry,
        connector_registry: Any,
        connector_broker: Any,
    ) -> None:
        self.config = config
        self.skills = skills
        self.tool_registry = tool_registry
        self.connector_registry = connector_registry
        self.connector_broker = connector_broker

    def research_prompt_tool_names(
        self,
        *,
        task: TaskSpec,
        request: RunRequest,
        allowed: set[str],
    ) -> tuple[list[str] | None, str | None]:
        _ = request
        if task.capability == "interface_analysis":
            interface_tools = {
                "parse_contract",
                "contract_inventory",
                "diff_contracts",
                "map_capabilities",
                "generate_synthetic_fixture",
                "run_contract_simulation",
            }
            return (
                sorted(name for name in allowed if name in interface_tools),
                "interface_agent_loop_tools",
            )
        if task.capability not in _RESEARCH_LOOP_CAPABILITIES:
            return None, None
        loop_tool_names = (
            {
                TOOL_WEB_SEARCH,
                "read_file",
                "list_files",
                "search_text",
            }
            | SOURCE_READ_TOOL_NAMES
            | EVIDENCE_BUILD_TOOL_NAMES
        )
        prompt = sorted(name for name in allowed if name in loop_tool_names)
        if set(prompt) == allowed:
            return None, None
        return prompt, "research_agent_loop_tools"

    def match_skills(self, *, request: RunRequest, task: TaskSpec) -> tuple[list[Any], bool]:
        skill_policy: dict[str, Any] = {}
        if is_registered_workflow(request.workflow_type):
            skill_policy = dict(resolve_workflow_pack(request.workflow_type).skill_policy)
        skills_disabled = request.metadata.get("disable_skills") == "true"
        if skills_disabled:
            return [], True
        skills = self.skills.match(
            capability=task.capability,
            required_skills=task.required_skills,
            skill_policy=skill_policy,
        )
        return skills, False

    def prepare_effective_policy(
        self,
        *,
        run_id: str,
        request: RunRequest,
        task: TaskSpec,
        existing_policy_data: dict[str, Any] | None,
        profile_digests: dict[str, str],
        stack_digest: str | None,
        stack_sha: str | None,
        stack_version: str | None,
    ) -> PreparedPolicy | BlockedPreparation:
        """Resolve or reuse EffectiveTaskPolicy. Does not persist or execute."""

        profile = resolve_task_model_profile(task, metadata=request.metadata)
        agent_profile = agent_profile_for(task.capability)
        skills, skills_disabled = self.match_skills(request=request, task=task)

        pack_id = None
        pack_version = None
        pack_policy_digest = None
        workflow_pack = None
        if is_registered_workflow(request.workflow_type):
            workflow_pack = resolve_workflow_pack(request.workflow_type)
            pack_id = workflow_pack.id
            pack_version = getattr(workflow_pack, "version", None)
            pack_policy_digest = workflow_pack.content_hash()

        if existing_policy_data:
            # Unfinished work created under an earlier policy schema is not
            # safe to resume.  The caller converts this into a durable blocked
            # state before any broker, model, or connector is constructed.
            try:
                existing = EffectiveTaskPolicy.model_validate(existing_policy_data)
                existing.ensure_valid_digest()
            except (TypeError, ValueError):
                return BlockedPreparation(
                    result=TaskResult(
                        task_id=task.id,
                        status="blocked",
                        summary="policy_incompatible: restart_required",
                        model_profile=profile,
                    ),
                    profile=profile,
                    agent_profile=agent_profile,
                    skills=skills,
                    skills_disabled=skills_disabled,
                )
            if (
                existing.schema_version != "effective_task_policy.v2"
                or existing.pack_id != pack_id
                or existing.pack_version != pack_version
                or existing.pack_policy_digest != pack_policy_digest
                or existing.capability != task.capability
                or existing.descriptor_version != require_descriptor(task.capability).version
            ):
                return BlockedPreparation(
                    result=TaskResult(
                        task_id=task.id,
                        status="blocked",
                        summary="policy_incompatible: restart_required",
                        model_profile=profile,
                    ),
                    profile=profile,
                    agent_profile=agent_profile,
                    skills=skills,
                    skills_disabled=skills_disabled,
                )
            return PreparedPolicy(
                effective_policy=existing,
                profile=profile,
                agent_profile=agent_profile,
                skills=skills,
                skills_disabled=skills_disabled,
                profile_digests=profile_digests,
                pack_id=pack_id,
                pack_version=pack_version,
                reused_existing=True,
            )

        assert workflow_pack is not None  # registered workflows only reach fresh resolve
        cap_policy = workflow_pack.execution_policy.capability_policies.get(task.capability)
        if cap_policy is None:
            return BlockedPreparation(
                result=TaskResult(
                    task_id=task.id,
                    status="unsupported",
                    summary=(
                        f"capability {task.capability!r} has no CapabilityExecutionPolicy "
                        f"in pack {workflow_pack.id!r}"
                    ),
                    model_profile=profile,
                ),
                profile=profile,
                agent_profile=agent_profile,
                skills=skills,
                skills_disabled=skills_disabled,
            )

        profile_cfg = self.config.models.profiles.get(profile)
        route_class = profile_cfg.route_class if profile_cfg is not None else "cloud"
        fallback_cfg = profile_cfg.cloud_fallback if profile_cfg is not None else None
        fallback_model_profile = (
            fallback_cfg.profile if fallback_cfg is not None and fallback_cfg.enabled else None
        )
        fallback_eligible = bool(fallback_cfg is not None and fallback_cfg.enabled)

        connector_tool_names = self.connector_registry.tool_names()
        grantable = grantable_connector_names_for_task(
            task=task,
            grantable_fn=self.connector_broker.grantable_tool_names,
            allowed_connector_classes=cap_policy.allowed_connector_classes or None,
        )
        allowed_preview, _, _ = compute_allowed_tool_names(
            task=task,
            request=request,
            tool_registry=self.tool_registry,
            connector_tool_names=connector_tool_names,
            grantable_connector_tools=grantable,
            web_search_tool=TOOL_WEB_SEARCH,
            denied_tool_names=workflow_pack.execution_policy.denied_tool_names,
            pack_allowed_tool_classes=cap_policy.allowed_tool_classes,
        )
        prompt_names, reduction_reason = self.research_prompt_tool_names(
            task=task,
            request=request,
            allowed=allowed_preview,
        )
        domain_packs = resolve_request_domain_packs(request, packs_root=self.config.root / "packs")
        policy_profiles = resolve_request_policy_profiles(
            request, profiles_root=self.config.root / "profiles"
        )
        composition_gate = evaluate_composition_gates(
            request=request,
            domain_packs=domain_packs,
            policy_profiles=policy_profiles,
            granted_tool_names=allowed_preview,
            granted_tool_classes={
                t.tool_class for t in self.tool_registry.list() if t.name in allowed_preview
            },
            skill_ids=[s.manifest.id for s in skills],
        )
        if not composition_gate.ok:
            return BlockedPreparation(
                result=TaskResult(
                    task_id=task.id,
                    status="failed",
                    summary="composition_conflict",
                    validator_results=[
                        ValidatorResult(
                            validator_id="composition_conflict",
                            status="fail",
                            message="Domain/policy composition conflict detected",
                            details={"conflicts": composition_gate.conflicts},
                        )
                    ],
                    model_profile=profile,
                ),
                profile=profile,
                agent_profile=agent_profile,
                skills=skills,
                skills_disabled=skills_disabled,
            )

        updated_digests = dict(profile_digests)
        updated_digests.update(composition_gate.profile_digests)
        cap_validators = list(cap_policy.validator_ids) if cap_policy.validator_ids else []
        effective_policy = resolve_effective_task_policy(
            run_id=run_id,
            task=task,
            request=request,
            tool_registry=self.tool_registry,
            model_profile=profile,
            agent_profile=agent_profile,
            skill_ids=[s.manifest.id for s in skills],
            pack_id=pack_id,
            pack_version=pack_version,
            pack_policy_digest=pack_policy_digest,
            descriptor_version=require_descriptor(task.capability).version,
            executor_adapter_id=require_descriptor(task.capability).executor_adapter_id,
            connector_tool_names=connector_tool_names,
            grantable_connector_tools=grantable,
            web_search_tool=TOOL_WEB_SEARCH,
            stack_profile_digest=stack_digest,
            stack_profile_artifact_sha256=stack_sha,
            stack_profile_schema_version=stack_version,
            reference_pack_ids=composition_gate.reference_pack_ids,
            profile_ids=[
                agent_profile,
                *composition_gate.policy_profile_ids,
            ],
            route_class=route_class,
            fallback_model_profile=fallback_model_profile,
            fallback_eligible=fallback_eligible,
            validator_ids=cap_validators
            or resolve_validation_command_ids(request)
            or list(self.config.policies.registered_commands),
            prompt_tool_names=prompt_names,
            prompt_reduction_reason=reduction_reason,
            denied_tool_names=workflow_pack.execution_policy.denied_tool_names,
            pack_allowed_tool_classes=cap_policy.allowed_tool_classes,
            executor_mode=cap_policy.executor_mode,
            repair_eligible=cap_policy.repair_eligible,
            approval_required=cap_policy.approval_required,
            external_action_requires_approval=cap_policy.external_action_requires_approval,
            allowed_connector_classes=cap_policy.allowed_connector_classes,
        )
        return PreparedPolicy(
            effective_policy=effective_policy,
            profile=profile,
            agent_profile=agent_profile,
            skills=skills,
            skills_disabled=skills_disabled,
            profile_digests=updated_digests,
            pack_id=pack_id,
            pack_version=pack_version,
            reused_existing=False,
        )

    def assemble_execution_request(
        self,
        *,
        run_id: str,
        run_dir: Path,
        request: RunRequest,
        task: TaskSpec,
        effective_policy: EffectiveTaskPolicy,
        agent_profile: str,
        model_profile: str,
        broker: Any,
        artifacts: Any,
        gateway: Any,
        raw_gateway: Any,
        allow_deterministic_workers: bool,
        ctx_messages: list[dict[str, str]],
        package_hash: str,
        granted_tool_names: set[str],
        registered_command_ids: list[str],
        dependency_outputs: list[dict[str, Any]] | None = None,
        repository_excerpts: list[dict[str, str]] | None = None,
        base_commit: str = "",
        land_map: Any = None,
        composer_role: str | None = None,
        validation_evidence_refs: list[str] | None = None,
        validator_results: list[dict[str, Any]] | None = None,
        composition: Any | None = None,
        services: dict[str, Any] | None = None,
    ) -> TaskExecutionRequest:
        """Build an immutable TaskExecutionRequest from prepared inputs."""

        return TaskExecutionRequest(
            run_id=run_id,
            run_dir=run_dir,
            request=request,
            task=task,
            effective_policy=effective_policy,
            descriptor=require_descriptor(task.capability),
            agent_profile=agent_profile,
            model_profile=model_profile,
            broker=broker,
            artifacts=artifacts,
            gateway=gateway,
            raw_gateway=raw_gateway,
            tool_registry=self.tool_registry,
            allow_deterministic_workers=allow_deterministic_workers,
            ctx_messages=ctx_messages,
            package_hash=package_hash,
            granted_tool_names=granted_tool_names,
            registered_command_ids=list(registered_command_ids),
            dependency_outputs=dependency_outputs or [],
            repository_excerpts=repository_excerpts or [],
            base_commit=base_commit,
            land_map=land_map,
            composer_role=composer_role,
            validation_evidence_refs=validation_evidence_refs or [],
            validator_results=validator_results or [],
            composition=composition,
            services=services or {},
        )
