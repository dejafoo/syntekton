"""Model-draft executors: review adapters and honest PM5 drafts (SD1.C/D)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from product_factory.context.safe_inventory import InventoryPolicy, build_safe_repository_inventory
from product_factory.domain.artifacts import ResourceRef
from product_factory.domain.findings import Finding
from product_factory.domain.tasks import TaskResult
from product_factory.domain.usage import UsageMetrics
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
    blocked_result,
)
from product_factory.gateway.canonical_messages import CanonicalMessage, ModelRequest
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.orchestration.review_findings import parse_raw_findings


def _execution_mode(request: TaskExecutionRequest) -> str:
    return "deterministic_mock" if request.allow_deterministic_workers else "live"


def _inventory_paths(request: TaskExecutionRequest) -> tuple[list[str], dict[str, Any]]:
    """Prefer SafeRepositoryInventory; fall back to broker list_files."""

    root = request.broker.original_repo or request.broker.worktree_root
    activity: dict[str, Any] = {}
    if root is not None and Path(root).exists():
        inventory = build_safe_repository_inventory(
            Path(root), policy=InventoryPolicy(max_files=200)
        )
        paths = inventory.relative_paths()[:80]
        activity = {
            "inventory_source": inventory.source,
            "inventory_policy_digest": inventory.policy_digest,
            "admitted_files": len(inventory.entries),
            "exclusions": len(inventory.exclusions),
        }
        return paths, activity
    if request.broker.worktree_root:
        listing = request.broker.execute(
            task_id=request.task.id,
            tool_name="list_files",
            arguments={"directory": ".", "glob": "**/*"},
        )
        paths = [
            str(entry.get("path", "")) if isinstance(entry, dict) else str(entry)
            for entry in listing.get("files", [])
        ][:80]
        activity = {"inventory_source": "list_files", "tool_call_id": listing.get("tool_call_id")}
        return paths, activity
    return [], {"inventory_source": "unavailable"}


class IndependentReviewExecutor:
    adapter_ids = frozenset({"independent_review"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        profile = request.model_profile
        tool_call_ids: list[str] = []
        task_findings: list[Finding] = []
        artifact_refs = []
        summary = ""
        result_status = "success"
        model_usage = UsageMetrics()
        dependency_outputs = request.dependency_outputs or []

        if broker.worktree_root is not None:
            diff = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
            tool_call_ids.append(diff["tool_call_id"])
            review_patch = diff.get("patch") or ""
            resource_type = "patch"
        else:
            review_patch = request.request.request_text
            for dependency in dependency_outputs:
                for ref in dependency.get("artifact_refs", []):
                    if ref.get("media_type") == "text/markdown" and ref.get("sha256"):
                        review_patch = artifacts.get_text(str(ref["sha256"]))
                        break
            resource_type = "task_result"
        patch_ref = ResourceRef(
            id=f"patch:{task.id}",
            resource_type=resource_type,
            origin="task",
            scope="review_input",
            trust_level="mixed",
            content_hash=patch_fingerprint(review_patch),
        )
        if resource_type == "patch" and review_patch:
            artifact_refs.append(
                artifacts.put_text(
                    review_patch,
                    media_type="text/x-diff",
                    logical_name=f"review-input-{task.id}.patch",
                    created_by_task_id=task.id,
                )
            )
        if request.allow_deterministic_workers:
            expect_blocking = (
                str(request.request.metadata.get("seed_review_expect_blocking") or "").lower()
                == "true"
            )
            seeded_paths = [
                p.strip()
                for p in str(request.request.metadata.get("seed_review_paths") or "").split(",")
                if p.strip()
            ]
            expect_flag = str(request.request.metadata.get("seed_review_expect_blocking") or "").lower()
            if expect_blocking and seeded_paths:
                evidence_path = seeded_paths[0]
                task_findings.append(
                    Finding(
                        id=f"F-{task.id}-seed",
                        category="correctness",
                        severity="blocking",
                        summary=f"Seeded correctness defect in {evidence_path}",
                        explanation=(
                            "Deterministic mock reviewer detected the seeded broken "
                            f"implementation at {evidence_path}."
                        ),
                        evidence_refs=[patch_ref.model_copy(update={"scope": evidence_path})],
                        recommended_action=f"Repair the defect in {evidence_path}",
                        confidence=0.9,
                        produced_by=profile,
                    )
                )
            elif expect_flag == "false" and seeded_paths:
                evidence_path = seeded_paths[0]
                task_findings.append(
                    Finding(
                        id=f"F-{task.id}-style",
                        category="maintainability",
                        severity="minor",
                        summary=f"Style-only note on {evidence_path}",
                        explanation="Cosmetic naming preference; not a correctness defect.",
                        evidence_refs=[patch_ref.model_copy(update={"scope": evidence_path})],
                        recommended_action="Optional rename; do not block merge",
                        confidence=0.8,
                        produced_by=profile,
                    )
                )
            else:
                task_findings.append(
                    Finding(
                        id=f"F-{task.id}",
                        category="correctness",
                        severity="minor",
                        summary="No blocking issues detected",
                        explanation="Deterministic mock reviewer inspected the inherited patch.",
                        evidence_refs=[patch_ref],
                        confidence=0.6,
                        produced_by=profile,
                        status="resolved",
                    )
                )
        else:
            review_messages = [
                CanonicalMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
                for m in request.ctx_messages
            ]
            review_messages.append(
                CanonicalMessage(
                    role="user",
                    content=(
                        "Review this patch independently. Return JSON with a findings array. "
                        "Each finding must contain category, severity, summary, explanation, "
                        "recommended_action, confidence, and evidence_path. Return an empty "
                        f"array when there are no issues.\n\n{review_patch}"
                    ),
                )
            )
            response = request.gateway.complete(
                ModelRequest(
                    request_id=f"review-{uuid.uuid4().hex[:8]}",
                    run_id=request.run_id,
                    task_id=task.id,
                    session_id=f"pf:{request.run_id}:{profile}:{task.id}",
                    model_profile=profile,
                    messages=review_messages,
                    output_schema={
                        "type": "object",
                        "properties": {
                            "findings": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {"type": "string"},
                                        "severity": {
                                            "type": "string",
                                            "enum": ["blocking", "major", "minor"],
                                        },
                                        "summary": {"type": "string"},
                                        "explanation": {"type": "string"},
                                        "recommended_action": {"type": "string"},
                                        "confidence": {"type": "number"},
                                        "evidence_path": {"type": "string", "minLength": 1},
                                    },
                                    "required": [
                                        "category",
                                        "severity",
                                        "summary",
                                        "explanation",
                                        "recommended_action",
                                        "confidence",
                                        "evidence_path",
                                    ],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["findings"],
                        "additionalProperties": False,
                    },
                    max_output_tokens=3000,
                    seed=(
                        int(request.request.metadata["benchmark_seed"])
                        if request.request.metadata.get("benchmark_seed") is not None
                        else None
                    ),
                )
            )
            model_usage = model_usage.merge(response.usage)
            payload = response.structured_data
            if payload is None and response.text:
                payload = json.loads(response.text)
            if response.status != "success" or payload is None:
                result_status = "failed"
                summary = f"review_{response.status}"
            else:
                task_findings.extend(
                    parse_raw_findings(
                        list(payload.get("findings") or []),
                        task_id=task.id,
                        produced_by=profile,
                        patch_ref=patch_ref,
                        review_patch=review_patch,
                        worktree_root=broker.worktree_root,
                        acceptance_criterion_ids=[ac.id for ac in task.acceptance_criteria],
                    )
                )
        art = artifacts.put_json(
            [finding.model_dump(mode="json") for finding in task_findings],
            logical_name="review-findings.json",
            created_by_task_id=task.id,
        )
        import shutil

        shutil.copy(artifacts.blobs / art.sha256, request.run_dir / "output" / "review-findings.json")
        artifact_refs.append(art)
        summary = summary or "Independent review complete"
        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status=result_status,  # type: ignore[arg-type]
                summary=summary,
                artifact_refs=artifact_refs,
                findings=task_findings,
                model_profile=profile,
                resolved_model_id=profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
                usage=model_usage,
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={"findings": len(task_findings), "parser": "review_findings.v1"},
        )


class SecurityReviewExecutor:
    adapter_ids = frozenset({"security_review"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        paths, inventory_activity = _inventory_paths(request)
        if not paths and not request.allow_deterministic_workers:
            return blocked_result(
                request,
                summary="security_review blocked: no SafeRepositoryInventory evidence",
                activity=inventory_activity,
            )
        findings = [
            {
                "id": f"SEC-{request.task.id}-scope",
                "severity": "info",
                "summary": "Repository scope reviewed for security evidence",
                "paths_reviewed": paths[:20],
                "label": "observation",
            }
        ]
        if any("secret" in path.lower() or path.endswith(".env") for path in paths):
            findings.append(
                {
                    "id": f"SEC-{request.task.id}-env",
                    "severity": "major",
                    "summary": "Environment or secrets-named path admitted for review",
                    "label": "inference",
                }
            )
        document = (
            "# Security evidence\n\n"
            "Hermetic security review grounded in SafeRepositoryInventory.\n\n"
            "## Findings\n\n"
            + "\n".join(f"- {item['summary']} ({item['label']})" for item in findings)
            + "\n"
        )
        art = request.artifacts.put_text(
            document,
            media_type="text/markdown",
            logical_name="security-evidence-draft.md",
            created_by_task_id=request.task.id,
            schema_id=request.descriptor.result_schema_id,
            schema_version="1",
        )
        receipt = request.artifacts.put_json(
            {"findings": findings, "inventory": inventory_activity},
            logical_name=f"security-review-receipt-{request.task.id}.json",
            created_by_task_id=request.task.id,
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary="Security review draft produced from safe inventory",
                artifact_refs=[art, receipt],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={**inventory_activity, "model": False, "parser": "security_findings.v1"},
        )


class DocumentationExecutor:
    adapter_ids = frozenset({"documentation"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        excerpts: list[str] = []
        for dependency in request.dependency_outputs or []:
            for ref in dependency.get("artifact_refs", []):
                sha = str(ref.get("sha256") or "")
                if not sha:
                    continue
                try:
                    excerpts.append(request.artifacts.get_text(sha)[:2000])
                except Exception:
                    continue
        if not excerpts and not request.repository_excerpts:
            # Ground on the run request text when no dependency artifacts exist
            # (intake framing packs). Still not a success-shaped stub: the draft
            # cites the request as untrusted input.
            if not (request.request.request_text or "").strip():
                return blocked_result(
                    request,
                    summary="documentation blocked: no verified dependency artifacts",
                    activity={"dependency_artifacts": 0},
                )
            body_parts = [request.request.request_text[:4000]]
        else:
            body_parts = excerpts or [
                f"{item.get('path')}: {item.get('content', '')[:400]}"
                for item in request.repository_excerpts[:10]
            ]
        document = (
            f"# Documentation draft\n\nObjective: {request.task.objective}\n\n"
            "## Grounded inputs\n\n"
            + "\n\n".join(f"```\n{part}\n```" for part in body_parts[:8])
            + "\n"
        )
        art = request.artifacts.put_text(
            document,
            media_type="text/markdown",
            logical_name=f"{request.task.id}-documentation.md",
            created_by_task_id=request.task.id,
            schema_id=request.descriptor.result_schema_id,
            schema_version="1",
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary="Documentation draft grounded in dependency artifacts",
                artifact_refs=[art],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={"grounded_excerpts": len(body_parts), "parser": "document_draft.v1"},
        )


class TestDesignExecutor:
    adapter_ids = frozenset({"test_design"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        paths, inventory_activity = _inventory_paths(request)
        if not paths:
            return blocked_result(
                request,
                summary="test_design blocked: SafeRepositoryInventory unavailable",
                activity=inventory_activity,
            )
        tests = [path for path in paths if "test" in Path(path).name.lower()][:20]
        entry_points = [
            path
            for path in paths
            if Path(path).name in {"main.py", "app.py", "cli.py", "index.ts", "package.json"}
        ][:20]
        plan = (
            "# Test plan\n\n"
            "Generated from SafeRepositoryInventory (not caller assertions).\n\n"
            "## Scope\n\n"
            f"- Admitted files considered: {len(paths)}\n"
            f"- Entry points: {', '.join(entry_points) or 'none observed'}\n"
            f"- Existing tests: {', '.join(tests) or 'none observed'}\n\n"
            "## Proposed coverage\n\n"
            "1. Exercise public entry points with hermetic fixtures.\n"
            "2. Assert failure modes for validation and authorization boundaries.\n"
            "3. Do not invent green validation results without execution receipts.\n"
        )
        art = request.artifacts.put_text(
            plan,
            media_type="text/markdown",
            logical_name="test-plan-draft.md",
            created_by_task_id=request.task.id,
            schema_id=request.descriptor.result_schema_id,
            schema_version="1",
        )
        scope = request.artifacts.put_json(
            {
                "files": paths[:50],
                "tests": tests,
                "entry_points": entry_points,
                "relevant_excerpts": request.repository_excerpts,
                "conventions": "Derived from SafeRepositoryInventory and targeted excerpts",
            },
            # Keep the historical logical name so quality-gate composition can
            # resolve scoped paths from dependency excerpts.
            logical_name="repository-analysis.json",
            created_by_task_id=request.task.id,
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary="Test plan drafted from safe inventory",
                artifact_refs=[art, scope],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={**inventory_activity, "parser": "test_plan.v1"},
        )


class ReleaseAnalysisExecutor:
    adapter_ids = frozenset({"release_analysis"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        granted = request.granted_tool_names
        ci_tools = {"get_commit_checks", "get_build_artifacts"} & granted
        tool_call_ids: list[str] = []
        actions: list[dict[str, Any]] = []
        data = request.request.pack_input or {}
        if not ci_tools:
            return blocked_result(
                request,
                summary="release_analysis blocked: ci_read tools not granted/enabled",
                activity={"granted_ci_tools": []},
            )
        repository = str(data.get("repository") or "").strip()
        commit_sha = str(data.get("commit_sha") or "").strip()
        if not repository or not commit_sha:
            return blocked_result(
                request,
                summary="release_analysis blocked: repository/commit_sha evidence missing",
                activity={"granted_ci_tools": sorted(ci_tools)},
            )
        if "get_commit_checks" in ci_tools:
            checks = request.broker.execute(
                task_id=request.task.id,
                tool_name="get_commit_checks",
                arguments={"repository": repository, "commit_sha": commit_sha},
            )
            tool_call_ids.append(checks["tool_call_id"])
            actions.append({"tool": "get_commit_checks", "result": checks})
        if "get_build_artifacts" in ci_tools:
            arts = request.broker.execute(
                task_id=request.task.id,
                tool_name="get_build_artifacts",
                arguments={"repository": repository, "commit_sha": commit_sha},
            )
            tool_call_ids.append(arts["tool_call_id"])
            actions.append({"tool": "get_build_artifacts", "result": arts})
        analysis = {
            "schema_id": "release_analysis.receipt.v1",
            "repository": repository,
            "commit_sha": commit_sha,
            "actions": actions,
            "labels": {"observation": True, "inference": False},
        }
        art = request.artifacts.put_json(
            analysis,
            logical_name=f"release-analysis-{request.task.id}.json",
            created_by_task_id=request.task.id,
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary="Release analysis completed from CI reads",
                artifact_refs=[art],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={"ci_tools": sorted(ci_tools), "parser": "release_analysis.v1"},
        )


class OperationsAnalysisExecutor:
    adapter_ids = frozenset({"operations_analysis"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        if "query_service_signals" not in request.granted_tool_names:
            return blocked_result(
                request,
                summary="operations_analysis blocked: ops_read tools not granted/enabled",
                activity={"granted_ops_tools": []},
            )
        data = request.request.pack_input or {}
        service_id = str(data.get("service_id") or data.get("service") or "").strip()
        environment = str(data.get("environment") or "").strip()
        window = data.get("time_window") or {}
        start = str((window.get("start") if isinstance(window, dict) else "") or data.get("start") or "").strip()
        end = str((window.get("end") if isinstance(window, dict) else "") or data.get("end") or "").strip()
        if not service_id or not environment or not start or not end:
            # Hermetic packs often omit ops bounds; stay honest.
            return blocked_result(
                request,
                summary=(
                    "operations_analysis blocked: service_id/environment/time_window required"
                ),
                activity={"reason": "missing_ops_bounds"},
            )
        result = request.broker.execute(
            task_id=request.task.id,
            tool_name="query_service_signals",
            arguments={
                "service_id": service_id,
                "environment": environment,
                "start": start,
                "end": end,
            },
        )
        payload = {
            "schema_id": "operations_analysis.receipt.v1",
            "service_id": service_id,
            "environment": environment,
            "time_window": {"start": start, "end": end},
            "result": result,
            "labels": {"observation": True, "staleness": "connector_bounded"},
        }
        art = request.artifacts.put_json(
            payload,
            logical_name=f"operations-analysis-{request.task.id}.json",
            created_by_task_id=request.task.id,
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary="Operations analysis completed from bounded ops reads",
                artifact_refs=[art],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=[result["tool_call_id"]],
            ),
            request=request,
            execution_mode=_execution_mode(request),
            activity={"ops_tool": "query_service_signals", "parser": "operations_analysis.v1"},
        )


class ModelDraftExecutor:
    executor_mode = "model_draft"
    adapter_ids = frozenset(
        {
            "independent_review",
            "security_review",
            "documentation",
            "test_design",
            "release_analysis",
            "operations_analysis",
        }
    )

    def __init__(self) -> None:
        self._adapters: dict[str, Any] = {
            "independent_review": IndependentReviewExecutor(),
            "security_review": SecurityReviewExecutor(),
            "documentation": DocumentationExecutor(),
            "test_design": TestDesignExecutor(),
            "release_analysis": ReleaseAnalysisExecutor(),
            "operations_analysis": OperationsAnalysisExecutor(),
        }

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        adapter = request.descriptor.executor_adapter_id
        executor = self._adapters.get(adapter)
        if executor is None:
            return blocked_result(
                request,
                summary=f"unsupported model_draft adapter: {adapter}",
                activity={"reason": "unknown_adapter"},
            )
        return executor.execute(request)
