"""TaskPreparationService — immutable policy, prompt, dependency, and workspace preparation.

Owns capability/executor resolution inputs, effective-policy calculation, and
immutable ``TaskExecutionRequest`` construction. Does not execute tasks.

This service is the sole owner of inputs needed before runtime dispatch.  It
does not execute a task or commit its terminal lifecycle state.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from product_factory.config.loader import AppConfig
from product_factory.connectors.tavily import TOOL_WEB_SEARCH
from product_factory.context.assembler import (
    assemble_context,
    list_repository_paths,
    resolve_context_limits,
    select_repository_excerpts,
)
from product_factory.context.task_context import build_task_context, persist_task_context
from product_factory.domain.findings import ValidatorResult
from product_factory.domain.runs import RunRequest
from product_factory.domain.tasks import TaskResult, TaskSpec
from product_factory.executors.protocol import TaskExecutionRequest
from product_factory.executors.research_agent import (
    EVIDENCE_BUILD_TOOL_NAMES,
    SOURCE_READ_TOOL_NAMES,
)
from product_factory.orchestration.effective_policy import (
    EFFECTIVE_TASK_POLICY_SCHEMA,
    EffectiveTaskPolicy,
    compute_allowed_tool_names,
    grantable_connector_names_for_task,
    resolve_effective_task_policy,
)
from product_factory.orchestration.task_contracts import (
    DependencyArtifact,
    PreparedTask,
    PreparedWorkspace,
    TaskPreparationRequest,
)
from product_factory.orchestration.validation_repair.service import (
    resolve_validation_command_ids,
)
from product_factory.orchestration.worktree_lineage import WorktreeLineageService
from product_factory.persistence.artifact_policy import ArtifactInstance
from product_factory.policy.composition_gates import evaluate_composition_gates
from product_factory.policy.domain_packs import resolve_request_domain_packs
from product_factory.policy.policy_profiles import resolve_request_policy_profiles
from product_factory.policy.source_policy import resolve_request_source_policy
from product_factory.registry.capability_descriptors import (
    CAPABILITY_DESCRIPTORS,
    agent_profile_for,
    require_descriptor,
)
from product_factory.repository.stack_profile import StackProfile, discover_stack_profile
from product_factory.scheduling.scheduler import resolve_task_model_profile
from product_factory.schemas import validate_write_payload
from product_factory.skills.profiles import ProfileRegistry
from product_factory.skills.registry import SkillRegistry
from product_factory.tools.registry import ToolRegistry
from product_factory.workflows.registry import is_registered_workflow, resolve_workflow_pack

_RESEARCH_LOOP_CAPABILITIES = frozenset(
    capability_id
    for capability_id, descriptor in CAPABILITY_DESCRIPTORS.items()
    if descriptor.executor_mode == "research_agent_loop"
)


class TaskPreparationRepository(Protocol):
    """Narrow persistence needed while preparing immutable runtime input."""

    def get_task(self, run_id: str, task_id: str) -> dict[str, Any] | None: ...

    def list_artifact_instances(self, run_id: str) -> list[dict[str, Any]]: ...

    def record_artifact(self, artifact: dict[str, Any]) -> None: ...

    def upsert_task(self, **values: Any) -> None: ...


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
        repository: TaskPreparationRepository,
        worktree_lineage: WorktreeLineageService,
        on_artifact_instance: Callable[[ArtifactInstance], None],
        composition: Any,
    ) -> None:
        self.config = config
        self.skills = skills
        self.tool_registry = tool_registry
        self.connector_registry = connector_registry
        self.connector_broker = connector_broker
        self.repository = repository
        self.worktree_lineage = worktree_lineage
        self.on_artifact_instance = on_artifact_instance
        self.composition = composition

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

    def _pin_stack_profile(
        self,
        *,
        request: RunRequest,
        run_id: str,
        task_id: str,
        artifacts: Any,
        existing_policy: dict[str, Any] | None,
    ) -> tuple[dict[str, str], str | None, str | None, str | None]:
        """Resolve immutable profile digests, reusing the pinned stack on resume."""

        digests = ProfileRegistry.load(self.config.root / "profiles").digests()
        source_policy = resolve_request_source_policy(
            request, profiles_root=self.config.root / "profiles"
        )
        if source_policy is not None:
            digests.update(source_policy.as_manifest_entry())
        for pack in resolve_request_domain_packs(request, packs_root=self.config.root / "packs"):
            digests.update(pack.as_manifest_entry())
        for profile in resolve_request_policy_profiles(
            request, profiles_root=self.config.root / "profiles"
        ):
            digests.update(profile.as_manifest_entry())

        if existing_policy:
            stack_sha = existing_policy.get("stack_profile_artifact_sha256")
            stack_digest = existing_policy.get("stack_profile_digest")
            stack_version = existing_policy.get("stack_profile_schema_version")
            if stack_sha and artifacts.exists(str(stack_sha)):
                try:
                    pinned = StackProfile.model_validate(
                        json.loads(artifacts.get_text(str(stack_sha)))
                    )
                    digests.update(pinned.as_manifest_entry())
                    return digests, pinned.digest, str(stack_sha), pinned.version
                except (OSError, json.JSONDecodeError, ValueError, TypeError):
                    if stack_digest:
                        digests["stack:pinned"] = str(stack_digest)
                    return digests, stack_digest, str(stack_sha), stack_version
            if stack_sha:
                return digests, stack_digest, str(stack_sha), stack_version

        existing_instance = next(
            (
                row
                for row in self.repository.list_artifact_instances(run_id)
                if row.get("role") == "stack_profile"
            ),
            None,
        )
        if existing_instance and artifacts.exists(str(existing_instance["sha256"])):
            try:
                pinned = StackProfile.model_validate(
                    json.loads(artifacts.get_text(str(existing_instance["sha256"])))
                )
                digests.update(pinned.as_manifest_entry())
                return digests, pinned.digest, str(existing_instance["sha256"]), pinned.version
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                pass

        if request.repository_path is None:
            return digests, None, None, None

        stack_profile = discover_stack_profile(
            request.repository_path,
            registered_command_ids=(
                resolve_validation_command_ids(request) or self.config.policies.registered_commands
            ),
        )
        digests.update(stack_profile.as_manifest_entry())
        ref = artifacts.put_json(
            stack_profile.model_dump(mode="json"),
            logical_name="stack-profile.json",
            created_by_task_id=task_id,
            schema_id="stack_profile.v1",
            schema_version=stack_profile.version,
            trust_level="generated",
        )
        self.repository.record_artifact(ref.model_dump(mode="json"))
        self.on_artifact_instance(
            ArtifactInstance.create(
                run_id=run_id,
                sha256=ref.sha256,
                content_class="durable_output",
                capture_level="full",
                role="stack_profile",
                producer_task_id=task_id,
                media_type=ref.media_type,
                schema_id="stack_profile.v1",
                schema_version=stack_profile.version,
                size_bytes=ref.size_bytes,
                display_name="stack-profile.json",
                metadata={"stack_id": stack_profile.id, "digest": stack_profile.digest},
            )
        )
        return digests, stack_profile.digest, ref.sha256, stack_profile.version

    def prepare(
        self,
        preparation_request: TaskPreparationRequest,
    ) -> PreparedTask | BlockedPreparation:
        """Create the complete immutable input for one runtime dispatch."""

        session = preparation_request.execution_session
        task = preparation_request.task
        request = preparation_request.run_request
        run_id = session.run_id
        artifacts = session.artifacts
        run_dir = session.run_dir

        existing_policy_data: dict[str, Any] | None = None
        existing_row = self.repository.get_task(run_id, task.id)
        if existing_row and existing_row.get("effective_policy_json"):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                loaded = json.loads(existing_row["effective_policy_json"])
                if isinstance(loaded, dict):
                    existing_policy_data = loaded

        profile_digests, stack_digest, stack_sha, stack_version = self._pin_stack_profile(
            request=request,
            run_id=run_id,
            task_id=task.id,
            artifacts=artifacts,
            existing_policy=existing_policy_data,
        )
        policy = self.prepare_effective_policy(
            run_id=run_id,
            request=request,
            task=task,
            existing_policy_data=existing_policy_data,
            profile_digests=profile_digests,
            stack_digest=stack_digest,
            stack_sha=stack_sha,
            stack_version=stack_version,
        )
        if isinstance(policy, BlockedPreparation):
            return policy

        effective_policy = policy.effective_policy
        if not policy.reused_existing:
            validate_write_payload(
                EFFECTIVE_TASK_POLICY_SCHEMA,
                effective_policy.model_dump(mode="json"),
            )
            policy_ref = artifacts.put_json(
                effective_policy.model_dump(mode="json"),
                logical_name=f"effective-policy-{task.id}.json",
                created_by_task_id=task.id,
                schema_id=EFFECTIVE_TASK_POLICY_SCHEMA,
                schema_version="1",
            )
            self.repository.record_artifact(policy_ref.model_dump(mode="json"))
            self.on_artifact_instance(
                ArtifactInstance.create(
                    run_id=run_id,
                    sha256=policy_ref.sha256,
                    content_class="durable_output",
                    capture_level="full",
                    role="effective_task_policy",
                    producer_task_id=task.id,
                    media_type=policy_ref.media_type,
                    schema_id=EFFECTIVE_TASK_POLICY_SCHEMA,
                    schema_version="1",
                    size_bytes=policy_ref.size_bytes,
                    display_name=policy_ref.logical_name,
                )
            )
            self.repository.upsert_task(
                run_id=run_id,
                task_id=task.id,
                capability=task.capability,
                status="running",
                spec=task.model_dump(mode="json"),
                effective_policy=effective_policy.model_dump(mode="json"),
                active_operation=task.capability,
            )

        prompt_set = set(effective_policy.prompt_tool_names)
        tool_definitions = [
            {"name": item.name, "description": item.description, "parameters": item.input_schema}
            for item in self.tool_registry.list()
            if item.name in prompt_set
        ]
        registered_ids = resolve_validation_command_ids(request) or list(
            self.config.policies.registered_commands
        )
        if registered_ids:
            for entry in tool_definitions:
                if entry.get("name") == "run_validation_command":
                    entry["description"] = (
                        f"{entry.get('description', '')} Registered ids: "
                        f"{', '.join(registered_ids)}."
                    )

        excerpts: list[dict[str, str]] = []
        omissions: list[str] = ["skills_disabled"] if policy.skills_disabled else []
        profile_cfg = self.config.models.profiles.get(policy.profile)
        limits = resolve_context_limits(
            self.config.policies.context,
            task_max_input_tokens=task.budget.max_input_tokens,
            model_context_soft_limit=(
                profile_cfg.context_soft_limit if profile_cfg is not None else None
            ),
        )
        if preparation_request.original_repository is not None:
            context_mode = str(request.metadata.get("context_mode") or "targeted").strip().lower()
            if context_mode in {"file_list_only", "file-list-only", "paths_only"}:
                excerpts, inventory_omissions = list_repository_paths(
                    preparation_request.original_repository,
                    max_files=limits.max_file_list_paths,
                )
            else:
                excerpts, inventory_omissions = select_repository_excerpts(
                    preparation_request.original_repository,
                    objective=f"{request.request_text}\n{task.objective}",
                    max_files=limits.max_excerpt_files,
                    max_chars=limits.max_excerpt_chars,
                )
            omissions.extend(inventory_omissions)

        directives: list[str] = []
        if registered_ids and "run_validation_command" in effective_policy.allowed_tool_names:
            directives.append(
                "Validation: call run_validation_command only with a registered "
                f"command_id from [{', '.join(registered_ids)}]. Never use validator "
                "labels or raw executables."
            )
        if (
            effective_policy.workspace_access == "isolated_write"
            and "jitter" in f"{request.request_text}\n{task.objective}".lower()
        ):
            directives.append(
                "Retry/jitter: compute the jittered duration before calling sleep; "
                "tests assert that observed sleep values vary."
            )

        context = assemble_context(
            task=task,
            model_profile=policy.profile,
            agent_profile=policy.agent_profile,
            skills=policy.skills,
            tool_definitions=tool_definitions,
            repository_excerpts=excerpts,
            dependency_outputs=list(preparation_request.dependency_outputs),
            context_omissions=omissions,
            runtime_directives=directives or None,
            package_id=f"pkg-{task.id}",
            packing=limits,
            profile_digests=policy.profile_digests,
        )
        task_context = build_task_context(
            task_id=task.id,
            skills=policy.skills,
            tool_names=list(effective_policy.allowed_tool_names),
            prompt_tool_names=list(effective_policy.prompt_tool_names),
            expected_output_schema=task.expected_output_schema,
            profile_digests=policy.profile_digests,
            effective_policy=effective_policy.model_dump(mode="json"),
        )
        persist_task_context(task_context, run_dir / "prompts")
        (run_dir / "prompts" / f"{task.id}.manifest.json").write_text(
            context.manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        session.recorder.emit(
            run_id=run_id,
            event_type="prompt.package_created",
            task_id=task.id,
            summary="Prompt package assembled",
            payload={
                "package_hash": context.package_hash,
                "manifest": context.manifest.model_dump(mode="json"),
                "effective_policy": effective_policy.model_dump(mode="json"),
            },
            content=context.messages,
            content_logical_name=f"prompt-package-{task.id}",
        )

        workspace_path = run_dir / "scratch" / task.id
        workspace_path.mkdir(parents=True, exist_ok=True)
        inherited: list[str] = []
        conflicts: list[dict[str, str]] = []
        pre_patch_fingerprint: str | None = None
        if (
            effective_policy.workspace_access != "none"
            and preparation_request.worktrees is not None
            and preparation_request.original_repository is not None
            and preparation_request.base_commit
        ):
            workspace_path, inherited, conflicts, pre_patch_fingerprint = (
                self.worktree_lineage.prepare_task_worktree(
                    worktrees=preparation_request.worktrees,
                    artifacts=artifacts,
                    run_dir=run_dir,
                    task_id=task.id,
                    dependencies=task.dependencies,
                    dependency_outputs=list(preparation_request.dependency_outputs),
                    base_commit=preparation_request.base_commit,
                    writable=effective_policy.workspace_access == "isolated_write",
                    inherit_dependency_patches=True,
                )
            )
        if conflicts:
            return BlockedPreparation(
                result=TaskResult(
                    task_id=task.id,
                    status="failed",
                    summary="workspace_lineage_conflict",
                    validator_results=[
                        ValidatorResult(
                            validator_id="workspace_lineage_conflict",
                            status="fail",
                            message="Conflicting dependency patches detected",
                            details={"conflicts": conflicts},
                        )
                    ],
                    model_profile=policy.profile,
                ),
                profile=policy.profile,
                agent_profile=policy.agent_profile,
                skills=policy.skills,
                skills_disabled=policy.skills_disabled,
            )

        dependency_artifacts = tuple(
            DependencyArtifact(
                producer_task_id=str(dependency.get("task_id") or ""),
                role=preparation_request.land_map.role_for_logical_name(
                    str(ref.get("logical_name") or "")
                ),
                artifact_instance_id=None,
                sha256=str(ref.get("sha256") or ""),
                media_type=str(ref.get("media_type") or "application/octet-stream"),
                schema=str(ref.get("schema_id") or "") or None,
                verified_excerpt=next(
                    (
                        str(excerpt.get("content") or "")
                        for excerpt in dependency.get("artifact_excerpts", [])
                        if excerpt.get("sha256") == ref.get("sha256")
                    ),
                    None,
                ),
            )
            for dependency in preparation_request.dependency_outputs
            for ref in dependency.get("artifact_refs", [])
        )
        return PreparedTask(
            run_id=run_id,
            run_request=request,
            task_spec=task,
            descriptor=require_descriptor(task.capability),
            effective_policy=effective_policy,
            model_profile=policy.profile,
            agent_profile=policy.agent_profile,
            matched_skills=tuple(policy.skills),
            prompt_package=context,
            workspace=PreparedWorkspace(
                access_mode=effective_policy.workspace_access,
                root=workspace_path,
                base_revision=preparation_request.base_commit,
                inherited_artifact_instances=tuple(inherited),
                lineage=tuple(conflicts),
                pre_execution_patch_fingerprint=pre_patch_fingerprint,
                original_repository=preparation_request.original_repository,
            ),
            dependency_artifacts=dependency_artifacts,
            dependency_outputs=preparation_request.dependency_outputs,
            repository_excerpts=tuple(excerpts),
            registered_command_ids=tuple(registered_ids),
            land_map=preparation_request.land_map,
            composition_role=preparation_request.composition_role,
            validation_evidence_refs=preparation_request.validation_evidence_refs,
            validator_results=preparation_request.validator_results,
            composition=self.composition,
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
        deterministic_implementation=None,
        patch_changed_files=None,
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
            deterministic_implementation=deterministic_implementation,
            patch_changed_files=patch_changed_files,
        )
