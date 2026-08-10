"""Composition executor — pack handler compose or inherited patch assembly."""

from __future__ import annotations

import json

from product_factory.domain.tasks import TaskResult
from product_factory.domain.usage import UsageMetrics
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
)
from product_factory.gateway.mock import MockGateway
from product_factory.orchestration.composition.input import CompositionInput
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.repositories.patches import create_patch
from product_factory.schemas.builtin import ROLE_TO_SCHEMA
from product_factory.workflows.artifacts import (
    ROLE_CHANGE_BRIEF,
    ROLE_CLARIFICATION_REQUEST,
    ROLE_FEASIBILITY_DOSSIER,
    ROLE_PROPOSED_PATCH,
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
)
from product_factory.workflows.registry import is_registered_workflow

# SD1 temporary: compose role fallbacks until land_map owns all defaults
# (issue: remove-coordinator-compose-callbacks-2026-08).
_QUALITY_GATE_ROLES: dict[str, str] = {
    ROLE_TEST_PLAN: "TEST_PLAN.md",
    ROLE_QUALITY_FINDINGS: "QUALITY_FINDINGS.md",
    ROLE_SECURITY_EVIDENCE: "SECURITY_EVIDENCE.md",
    ROLE_FEASIBILITY_DOSSIER: "FEASIBILITY_DISCOVERY.md",
    ROLE_CHANGE_BRIEF: "CHANGE_BRIEF.md",
    ROLE_CLARIFICATION_REQUEST: "CLARIFICATION_REQUEST.md",
}


class CompositionExecutor:
    executor_mode = "composition"
    adapter_ids = frozenset({"composition"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        run_request = request.request
        run_dir = request.run_dir
        profile = request.model_profile
        package_hash = request.package_hash
        land_map = request.land_map
        composer_role = request.composer_role
        base_commit = request.base_commit
        dependency_outputs = request.dependency_outputs or []
        validation_evidence_refs = request.validation_evidence_refs
        validator_results = request.validator_results
        execution_mode = "deterministic_mock" if request.allow_deterministic_workers else "live"

        artifact_refs = []
        summary = ""
        result_status: str = "success"
        model_usage = UsageMetrics()
        task_findings = []

        if (
            is_registered_workflow(run_request.workflow_type)
            and composer_role
            and composer_role != ROLE_PROPOSED_PATCH
            and land_map is not None
        ):
            document_name = land_map.logical_name_for(
                composer_role,
                default=_QUALITY_GATE_ROLES.get(composer_role, f"{composer_role}.md"),
            )
            use_mock = isinstance(request.raw_gateway, MockGateway)
            composition = request.composition
            if composition is None:
                raise RuntimeError("composition executor requires CompositionService")
            composition_input = CompositionInput(
                request=run_request,
                role=composer_role,
                document_name=document_name,
                findings=tuple(task_findings),
                dependency_outputs=tuple(dependency_outputs),
                use_mock=use_mock,
                task=task,
                gateway=request.gateway,
                context_messages=tuple(request.ctx_messages),
                run_id=request.run_id,
                profile=profile,
                base_revision=base_commit,
                validation_evidence_refs=tuple(validation_evidence_refs),
                validator_results=tuple(validator_results),
            )
            composed = composition.compose(composition_input)
            document = composed.body
            model_usage = model_usage.merge(composed.usage)
            schema_id = ROLE_TO_SCHEMA.get(composer_role)
            art = artifacts.put_text(
                document,
                media_type=composed.media_type,
                logical_name=document_name,
                created_by_task_id=task.id,
                schema_id=schema_id,
                schema_version="1" if schema_id else None,
                handoff_state="draft",
            )
            artifact_refs.append(art)
            summary = f"{composer_role} composed"
        else:
            if broker.worktree_root and base_commit and land_map is not None:
                patch = create_patch(broker.worktree_root, base_commit)
                art = artifacts.put_text(
                    patch,
                    media_type="text/x-diff",
                    logical_name=land_map.logical_name_for(
                        ROLE_PROPOSED_PATCH, default="proposed.patch"
                    ),
                    created_by_task_id=task.id,
                    schema_id=ROLE_TO_SCHEMA.get(ROLE_PROPOSED_PATCH),
                    schema_version="1",
                    handoff_state="draft",
                )
                artifact_refs.append(art)
                summary = "Patch composed" if patch.strip() else "Empty patch composed"
                if not patch.strip():
                    result_status = "failed"
                lineage_path = run_dir / "output" / f"{task.id}-lineage.json"
                if lineage_path.exists():
                    lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
                    lineage["final_patch_fingerprint"] = patch_fingerprint(patch) if patch else None
                    lineage["post_patch_fingerprint"] = lineage["final_patch_fingerprint"]
                    lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
            else:
                summary = "Nothing to compose"

        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status=result_status,  # type: ignore[arg-type]
                summary=summary,
                artifact_refs=artifact_refs,
                findings=task_findings,
                model_profile=profile,
                resolved_model_id=profile,
                provider=getattr(request.gateway, "default_model", type(request.gateway).__name__),
                prompt_package_hash=package_hash,
                usage=model_usage,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={"composer_role": composer_role},
        )
