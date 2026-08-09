"""Validation executor — registered command receipts only (SD1.D / SR0.B)."""

from __future__ import annotations

from typing import Any

from product_factory.domain.errors import ToolAuthorizationError
from product_factory.domain.findings import Finding, ValidatorResult
from product_factory.domain.tasks import TaskResult
from product_factory.executors.protocol import (
    TaskExecutionRequest,
    attach_receipt,
    blocked_result,
)


class TestExecutionExecutor:
    executor_mode = "validation"
    adapter_ids = frozenset({"test_execution"})

    def execute(self, request: TaskExecutionRequest) -> TaskResult:
        command_ids = list(request.registered_command_ids)
        if not command_ids:
            return blocked_result(
                request,
                summary="test_execution blocked: no registered validation commands",
                activity={"registered_commands": []},
            )
        if "run_validation_command" not in request.granted_tool_names:
            return blocked_result(
                request,
                summary="test_execution blocked: run_validation_command not granted",
                activity={"registered_commands": command_ids},
            )

        tool_call_ids: list[str] = []
        receipts: list[dict[str, Any]] = []
        findings: list[Finding] = []
        validator_results: list[ValidatorResult] = []
        domain_failures = 0
        execution_mode = "deterministic_mock" if request.allow_deterministic_workers else "live"

        for command_id in command_ids:
            try:
                result = request.broker.execute(
                    task_id=request.task.id,
                    tool_name="run_validation_command",
                    arguments={"command_id": command_id},
                )
            except ToolAuthorizationError as exc:
                return blocked_result(
                    request,
                    summary=f"test_execution blocked: {exc}",
                    execution_mode=execution_mode,
                    activity={
                        "registered_commands": command_ids,
                        "failed_command_id": command_id,
                        "reason": "authorization",
                    },
                )
            except (FileNotFoundError, OSError, RuntimeError) as exc:
                return attach_receipt(
                    TaskResult(
                        task_id=request.task.id,
                        status="failed",
                        summary=(
                            f"test_execution failed: could not start or observe "
                            f"command {command_id!r}: {exc}"
                        ),
                        model_profile=request.model_profile,
                        resolved_model_id=request.model_profile,
                        prompt_package_hash=request.package_hash,
                        tool_call_ids=tool_call_ids,
                    ),
                    request=request,
                    execution_mode=execution_mode,
                    activity={
                        "commands": command_ids,
                        "failed_command_id": command_id,
                        "reason": "start_or_observe",
                    },
                )

            tool_call_id = str(result.get("tool_call_id") or "")
            if tool_call_id:
                tool_call_ids.append(tool_call_id)
            execution_state = str(result.get("execution_state") or "")
            if execution_state in {"timeout", "unavailable"}:
                return blocked_result(
                    request,
                    summary=(
                        f"test_execution blocked: command {command_id!r} "
                        f"is {execution_state}"
                    ),
                    execution_mode=execution_mode,
                    activity={
                        "commands": command_ids,
                        "failed_command_id": command_id,
                        "reason": execution_state,
                    },
                )
            exit_code = result.get("exit_code")
            if exit_code is None:
                exit_code = result.get("returncode")
            # Missing mandatory observation of the command outcome is not success.
            if exit_code is None and result.get("status") not in {"success", "failed", "error"}:
                return attach_receipt(
                    TaskResult(
                        task_id=request.task.id,
                        status="failed",
                        summary=(
                            f"test_execution failed: mandatory receipt incomplete "
                            f"for command {command_id!r} (missing exit_code)"
                        ),
                        model_profile=request.model_profile,
                        resolved_model_id=request.model_profile,
                        prompt_package_hash=request.package_hash,
                        tool_call_ids=tool_call_ids,
                    ),
                    request=request,
                    execution_mode=execution_mode,
                    activity={
                        "commands": command_ids,
                        "failed_command_id": command_id,
                        "reason": "incomplete_receipt",
                    },
                )

            ok = False
            if exit_code is not None:
                ok = int(exit_code) == 0
            elif result.get("status") == "success":
                ok = True

            receipt = {
                "command_id": command_id,
                "tool_call_id": tool_call_id,
                "exit_code": exit_code,
                "ok": bool(ok),
                "stdout_excerpt": str(result.get("stdout") or "")[:500],
                "stderr_excerpt": str(result.get("stderr") or "")[:500],
                "timed_out": int(exit_code) == 124 if exit_code is not None else False,
                "execution_state": execution_state or "domain_failure",
                "validation_evidence_ref": result.get("validation_evidence_ref"),
            }
            receipts.append(receipt)
            if not ok:
                domain_failures += 1
                message = f"Validation command {command_id} failed (exit_code={exit_code})"
                validator_results.append(
                    ValidatorResult(
                        validator_id=f"validation_command:{command_id}",
                        status="fail",
                        message=message,
                        details={
                            "command_id": command_id,
                            "exit_code": exit_code,
                            "timed_out": receipt["timed_out"],
                        },
                    )
                )
                findings.append(
                    Finding(
                        id=f"validation-{request.task.id}-{command_id}",
                        category="correctness",
                        severity="blocking",
                        summary=f"Validation command failed: {command_id}",
                        explanation=message,
                        status="open",
                        produced_by="test_execution",
                        evidence_refs=[],
                        recommended_action="Inspect validation receipts and fix failing tests",
                    )
                )

        if not receipts:
            return blocked_result(
                request,
                summary="test_execution blocked: no command receipts captured",
                execution_mode=execution_mode,
                activity={"registered_commands": command_ids},
            )

        art = request.artifacts.put_json(
            {
                "schema_id": "validation_receipt.v1",
                "receipts": receipts,
                "synthesized_pass": False,
                "domain_failures": domain_failures,
            },
            logical_name=f"validation-receipts-{request.task.id}.json",
            created_by_task_id=request.task.id,
        )
        # Complete observed receipts are successful executor completion even
        # when one or more commands failed. Domain outcome lives in receipts,
        # validator_results, and findings — not in a partial task status.
        summary = (
            f"Validation receipts recorded with failures ({domain_failures})"
            if domain_failures
            else f"Validation receipts recorded for {len(receipts)} command(s)"
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status="success",
                summary=summary,
                artifact_refs=[art],
                findings=findings,
                validator_results=validator_results,
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={
                "commands": command_ids,
                "domain_failures": domain_failures,
                "parser": "validation_receipt.v1",
            },
        )
