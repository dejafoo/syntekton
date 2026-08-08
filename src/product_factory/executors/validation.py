"""Validation executor — registered command receipts only (SD1.D)."""

from __future__ import annotations

from typing import Any

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
        failed = False
        for command_id in command_ids:
            result = request.broker.execute(
                task_id=request.task.id,
                tool_name="run_validation_command",
                arguments={"command_id": command_id},
            )
            tool_call_ids.append(result["tool_call_id"])
            exit_code = result.get("exit_code")
            if exit_code is None:
                exit_code = result.get("returncode")
            ok = exit_code == 0 or result.get("status") == "success"
            receipts.append(
                {
                    "command_id": command_id,
                    "tool_call_id": result["tool_call_id"],
                    "exit_code": exit_code,
                    "ok": bool(ok),
                    "stdout_excerpt": str(result.get("stdout") or "")[:500],
                    "stderr_excerpt": str(result.get("stderr") or "")[:500],
                }
            )
            if not ok:
                failed = True

        art = request.artifacts.put_json(
            {
                "schema_id": "validation_receipt.v1",
                "receipts": receipts,
                "synthesized_pass": False,
            },
            logical_name=f"validation-receipts-{request.task.id}.json",
            created_by_task_id=request.task.id,
        )
        status = "partial" if failed else "success"
        summary = (
            f"Validation receipts recorded with failures ({sum(1 for r in receipts if not r['ok'])})"
            if failed
            else f"Validation receipts recorded for {len(receipts)} command(s)"
        )
        execution_mode = (
            "deterministic_mock" if request.allow_deterministic_workers else "live"
        )
        return attach_receipt(
            TaskResult(
                task_id=request.task.id,
                status=status,  # type: ignore[arg-type]
                summary=summary,
                artifact_refs=[art],
                model_profile=request.model_profile,
                resolved_model_id=request.model_profile,
                prompt_package_hash=request.package_hash,
                tool_call_ids=tool_call_ids,
            ),
            request=request,
            execution_mode=execution_mode,
            activity={
                "commands": command_ids,
                "failed": failed,
                "parser": "validation_receipt.v1",
            },
        )
