"""Repository agent-loop executor for implementation and repair (SD1.C)."""

from __future__ import annotations

import json

from product_factory.domain.errors import (
    BudgetExhaustedError,
    SkillGrantViolation,
    ToolAuthorizationError,
)
from product_factory.domain.tasks import TaskResult
from product_factory.domain.usage import UsageMetrics
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
)
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolDefinition,
)
from product_factory.orchestration.agent_loop import run_tool_agent
from product_factory.orchestration.implementation_helpers import (
    deterministic_impl_files,
    extract_unified_diff,
)
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.repositories.patches import create_patch


class RepositoryAgentExecutor:
    executor_mode = "repository_agent_loop"
    adapter_ids = frozenset({"repository_agent"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        broker = request.broker
        artifacts = request.artifacts
        task = request.task
        run_request = request.request
        run_dir = request.run_dir
        run_id = request.run_id
        profile = request.model_profile
        gateway = request.gateway
        package_hash = request.package_hash
        granted = request.granted_tool_names
        registered_ids = list(request.registered_command_ids)
        base_commit = request.base_commit
        allow_mock = request.allow_deterministic_workers
        execution_mode = "deterministic_mock" if allow_mock else "live"

        changed_files: list[str] = []
        artifact_refs = []
        summary = ""
        result_status: str = "success"
        tool_call_ids: list[str] = []
        model_usage = UsageMetrics()

        if not broker.worktree_root:
            return attach_receipt(
                TaskResult(
                    task_id=task.id,
                    status="blocked",
                    summary=f"{task.capability} requires a worktree",
                    model_profile=profile,
                    resolved_model_id=profile,
                    prompt_package_hash=package_hash,
                    usage=model_usage,
                ),
                request=request,
                execution_mode=execution_mode,
                activity={"reason": "missing_worktree"},
            )

        applied = False
        seeded_defect = str(run_request.metadata.get("seed_repair_defect") or "").strip()
        force_seeded_impl = (
            task.capability == "implementation"
            and run_request.metadata.get("force_seeded_impl") == "true"
            and bool(seeded_defect)
        )
        if force_seeded_impl:
            from product_factory.evaluation.defects import (
                defect_files,
                resolve_defect_kind,
            )

            case_id = str(run_request.metadata.get("eval_case") or "")
            kind = resolve_defect_kind(case_id, explicit=seeded_defect)
            for rel_path, content in defect_files(case_id or "code_cache", kind):
                out = broker.execute(
                    task_id=task.id,
                    tool_name="create_file",
                    arguments={
                        "path": rel_path,
                        "content": content,
                        "overwrite": True,
                    },
                )
                tool_call_ids.append(out["tool_call_id"])
                changed_files.append(rel_path)
            applied = True
            summary = f"Seeded repairable defect ({kind})"
            artifact_refs.append(
                artifacts.put_json(
                    {
                        "seed_repair_defect": kind,
                        "case_id": case_id,
                        "files": changed_files,
                    },
                    logical_name=f"seeded-defect-{task.id}.json",
                    created_by_task_id=task.id,
                )
            )
        elif not allow_mock:
            patch_text = ""
            changed_files_from_patch = request.services.get("changed_files_from_patch")
            try:
                impl_messages = [
                    CanonicalMessage(role=m["role"], content=m["content"])  # type: ignore[arg-type]
                    for m in request.ctx_messages
                ]
                impl_messages.append(
                    CanonicalMessage(
                        role="user",
                        content=(
                            "Implement the task now. Inspect relevant repository files before "
                            "editing. Use the provided tools to modify the worktree and inspect "
                            "the final diff. Finish with a concise summary after the worktree "
                            "contains the complete change."
                            + (
                                " When re-checking tests, call run_validation_command with a "
                                f"registered command_id only ({', '.join(registered_ids)})."
                                if task.capability == "repair" and registered_ids
                                else ""
                            )
                        ),
                    )
                )
                canonical_tools = [
                    CanonicalToolDefinition(
                        name=definition.name,
                        description=(
                            f"{definition.description} Registered ids: "
                            f"{', '.join(registered_ids)}."
                            if definition.name == "run_validation_command" and registered_ids
                            else definition.description
                        ),
                        parameters=definition.input_schema,
                    )
                    for definition in request.tool_registry.list()
                    if definition.name in granted
                ]
                loop = run_tool_agent(
                    gateway=gateway,
                    broker=broker,
                    run_id=run_id,
                    task_id=task.id,
                    session_id=f"pf:{run_id}:{profile}:{task.id}",
                    model_profile=profile,
                    messages=impl_messages,
                    tools=canonical_tools,
                    max_rounds=min(16, task.budget.max_tool_calls + 1),
                    max_tool_calls=task.budget.max_tool_calls,
                    max_cost_usd=task.budget.max_cost_usd,
                    max_input_tokens=task.budget.max_input_tokens,
                    max_output_tokens=task.budget.max_output_tokens,
                    timeout_seconds=task.budget.max_wall_clock_seconds,
                    seed=(
                        int(run_request.metadata["benchmark_seed"])
                        if run_request.metadata.get("benchmark_seed") is not None
                        else None
                    ),
                )
                model_usage = model_usage.merge(loop.usage)
                tool_call_ids.extend(loop.tool_call_ids)
                artifact_refs.append(
                    artifacts.put_json(
                        loop.model_dump(mode="json"),
                        logical_name=f"agent-loop-{task.id}.json",
                        created_by_task_id=task.id,
                    )
                )
                patch_text = extract_unified_diff(loop.final_text)
                if patch_text and not any(
                    tc.tool_name in {"create_file", "apply_patch"} for tc in broker.history
                ):
                    out = broker.execute(
                        task_id=task.id,
                        tool_name="apply_patch",
                        arguments={"patch": patch_text},
                    )
                    tool_call_ids.append(out["tool_call_id"])
                    if callable(changed_files_from_patch):
                        changed_files.extend(
                            list(changed_files_from_patch(patch_text))  # type: ignore[arg-type]
                        )
                diff_probe = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
                tool_call_ids.append(diff_probe["tool_call_id"])
                applied = bool((diff_probe.get("patch") or "").strip())
                if applied:
                    changed_files.extend(list(diff_probe.get("changed_files") or []))
                    summary = (
                        f"Implementation agent completed in {loop.rounds} rounds "
                        f"({loop.termination_reason})"
                    )
                else:
                    summary = (
                        "invalid_patch_format"
                        if loop.final_text.strip()
                        else (
                            loop.termination_reason
                            if loop.termination_reason
                            in {
                                "budget_exhausted",
                                "token_budget_exhausted",
                                "tool_budget_exhausted",
                                "no_progress",
                                "timeout",
                            }
                            else "empty_model_output"
                        )
                    )
            except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
                raise
            except Exception as exc:
                reason = "patch_apply_failed" if patch_text else "provider_failed"
                summary = f"{reason}: {exc}"

        # Deterministic implementations are test fixtures, never a live fallback.
        if not applied and allow_mock:
            impl_files = request.services.get("deterministic_impl_files")
            if callable(impl_files):
                file_pairs = impl_files(
                    run_request.request_text, task_objective=task.objective
                )
            else:
                file_pairs = deterministic_impl_files(
                    run_request.request_text, task_objective=task.objective
                )
            for rel_path, content in list(file_pairs):  # type: ignore[arg-type]
                try:
                    out = broker.execute(
                        task_id=task.id,
                        tool_name="create_file",
                        arguments={
                            "path": rel_path,
                            "content": content,
                            "overwrite": True,
                        },
                    )
                    tool_call_ids.append(out["tool_call_id"])
                    changed_files.append(rel_path)
                except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
                    raise
                except Exception as exc:
                    summary = f"Implementation write failed: {exc}"
            summary = summary or "Implementation files written (deterministic)"
        elif not applied:
            result_status = "failed"
            summary = summary or "empty_model_output"

        grant = broker.grants.get(task.id)
        if grant is not None:
            grant.max_calls = max(grant.max_calls, grant.calls_made + 3)
        try:
            diff = broker.execute(task_id=task.id, tool_name="git_diff", arguments={})
            tool_call_ids.append(diff["tool_call_id"])
            patch_body = diff.get("patch") or ""
            changed_from_diff = diff.get("changed_files") or []
            artifact_sha = diff.get("artifact_sha256")
        except (BudgetExhaustedError, SkillGrantViolation, ToolAuthorizationError):
            raise
        except Exception:
            patch_body = (
                create_patch(broker.worktree_root, base_commit)
                if broker.worktree_root and base_commit
                else ""
            )
            changed_from_diff = [
                line[6:] for line in patch_body.splitlines() if line.startswith("+++ b/")
            ]
            artifact_sha = None
        if patch_body.strip():
            art = artifacts.put_text(
                patch_body,
                media_type="text/x-diff",
                logical_name="implementation.patch",
                created_by_task_id=task.id,
            )
            if artifact_sha is None:
                artifact_sha = art.sha256
            artifact_refs.append(art)
            (run_dir / "output" / "implementation.patch").write_text(
                patch_body, encoding="utf-8"
            )
            if not changed_files:
                changed_files.extend(changed_from_diff)
            lineage_path = run_dir / "output" / f"{task.id}-lineage.json"
            if lineage_path.exists():
                lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
                lineage["post_patch_fingerprint"] = patch_fingerprint(patch_body)
                lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
        elif not allow_mock:
            result_status = "failed"
            summary = summary or "invalid_patch_format"
        summary = summary or "Implementation patch produced"

        return attach_receipt(
            TaskResult(
                task_id=task.id,
                status=result_status,  # type: ignore[arg-type]
                summary=summary,
                artifact_refs=artifact_refs,
                changed_files=changed_files,
                model_profile=profile,
                resolved_model_id=profile,
                provider=getattr(gateway, "default_model", type(gateway).__name__),
                prompt_package_hash=package_hash,
                tool_call_ids=tool_call_ids,
                usage=model_usage,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={
                "tools": sorted(
                    {tc.tool_name for tc in broker.history if getattr(tc, "tool_name", None)}
                ),
                "changed_files": list(changed_files),
            },
        )
