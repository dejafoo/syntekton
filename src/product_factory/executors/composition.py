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
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.repositories.patches import create_patch
from product_factory.schemas.builtin import ROLE_TO_SCHEMA
from product_factory.workflows.artifacts import (
    ROLE_CHANGE_BRIEF,
    ROLE_CHANGE_SET,
    ROLE_CLARIFICATION_REQUEST,
    ROLE_DEPLOYMENT_RECORD,
    ROLE_FEASIBILITY_DOSSIER,
    ROLE_PROPOSED_PATCH,
    ROLE_QUALITY_FINDINGS,
    ROLE_SECURITY_EVIDENCE,
    ROLE_TEST_PLAN,
    ROLE_VERIFICATION_REPORT,
)
from product_factory.workflows.handlers import handler_for
from product_factory.workflows.handlers.base import ComposeContext
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
        services = request.services
        execution_mode = (
            "deterministic_mock" if request.allow_deterministic_workers else "live"
        )

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
            handler = handler_for(run_request.workflow_type)
            document_name = land_map.logical_name_for(
                composer_role,
                default=_QUALITY_GATE_ROLES.get(composer_role, f"{composer_role}.md"),
            )
            use_mock = isinstance(request.raw_gateway, MockGateway)
            gen_usage_box: list[UsageMetrics] = []
            generate_architecture = services.get("generate_architecture_document")

            def _generate() -> tuple[str, UsageMetrics]:
                if not callable(generate_architecture):
                    raise RuntimeError(
                        "composition requires generate_architecture_document service"
                    )
                text, usage = generate_architecture(  # type: ignore[misc]
                    request=run_request,
                    task=task,
                    ctx_messages=request.ctx_messages,
                    run_id=request.run_id,
                    profile=profile,
                    dependency_outputs=dependency_outputs,
                    document_name=document_name,
                    gateway=request.gateway,
                )
                gen_usage_box.append(usage)
                return text, usage

            compose_ctx = ComposeContext(
                request=run_request,
                role=composer_role,
                document_name=document_name,
                findings=task_findings,
                dependency_outputs=dependency_outputs,
                use_mock=use_mock,
                generate_architecture=_generate if not use_mock else None,
                compose_architecture=services.get("compose_architecture"),
                compose_evidence_report=services.get("compose_evidence_report"),
                compose_feasibility_dossier=services.get("compose_feasibility_dossier"),
                compose_change_intake=services.get("compose_change_intake"),
                compose_quality_document=services.get("compose_quality_document"),
                task=task,
                ctx_messages=request.ctx_messages,
                run_id=request.run_id,
                profile=profile,
                base_revision=base_commit,
                validation_evidence_refs=validation_evidence_refs,
                validator_results=validator_results,
            )
            document = handler.compose(composer_role, compose_ctx)
            if gen_usage_box:
                model_usage = model_usage.merge(gen_usage_box[0])
            schema_id = ROLE_TO_SCHEMA.get(composer_role)
            media_type = (
                "application/json"
                if composer_role
                in {
                    ROLE_CHANGE_SET,
                    ROLE_DEPLOYMENT_RECORD,
                    ROLE_VERIFICATION_REPORT,
                }
                else "text/markdown"
            )
            art = artifacts.put_text(
                document,
                media_type=media_type,
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
                    lineage["final_patch_fingerprint"] = (
                        patch_fingerprint(patch) if patch else None
                    )
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
                provider=getattr(
                    request.gateway, "default_model", type(request.gateway).__name__
                ),
                prompt_package_hash=package_hash,
                usage=model_usage,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={"composer_role": composer_role},
        )
