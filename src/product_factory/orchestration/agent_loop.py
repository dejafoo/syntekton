"""Bounded provider-neutral tool loop for implementation and repair tasks."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from decimal import Decimal

from pydantic import BaseModel, Field

from product_factory.domain.usage import UsageMetrics
from product_factory.gateway.base import ModelGateway
from product_factory.gateway.canonical_messages import (
    CanonicalMessage,
    CanonicalToolDefinition,
    ModelRequest,
)
from product_factory.orchestration.repair import patch_fingerprint
from product_factory.tools.broker import ToolBroker


class AgentLoopResult(BaseModel):
    status: str
    final_text: str = ""
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    rounds: int = 0
    tool_call_ids: list[str] = Field(default_factory=list)
    termination_reason: str
    error: str | None = None


def _write_fingerprint(name: str, arguments: dict) -> str | None:
    if name == "apply_patch":
        patch = str(arguments.get("patch") or "")
        return patch_fingerprint(patch) if patch.strip() else None
    if name == "create_file":
        return patch_fingerprint(
            f"{arguments.get('path', '')}\n{arguments.get('content', '')}"
        )
    return None


def run_tool_agent(
    *,
    gateway: ModelGateway,
    broker: ToolBroker,
    run_id: str,
    task_id: str,
    session_id: str,
    model_profile: str,
    messages: list[CanonicalMessage],
    tools: list[CanonicalToolDefinition],
    max_rounds: int,
    max_tool_calls: int,
    max_cost_usd: Decimal,
    max_input_tokens: int,
    max_output_tokens: int,
    timeout_seconds: int,
    seed: int | None = None,
) -> AgentLoopResult:
    """Run model → tools → model until a final response or a bounded stop."""
    history = list(messages)
    usage = UsageMetrics()
    tool_call_ids: list[str] = []
    seen_calls: dict[str, int] = {}
    seen_patch_fps: dict[str, int] = {}
    inspected_repository = False
    started = time.monotonic()
    unregistered_command_failures = 0

    for round_no in range(1, max_rounds + 1):
        if time.monotonic() - started >= timeout_seconds:
            return AgentLoopResult(
                status="failed",
                usage=usage,
                rounds=round_no - 1,
                tool_call_ids=tool_call_ids,
                termination_reason="timeout",
                error="Agent loop wall-clock deadline exceeded",
            )
        remaining = max_cost_usd - usage.estimated_cost_usd
        if remaining <= 0:
            return AgentLoopResult(
                status="failed",
                usage=usage,
                rounds=round_no - 1,
                tool_call_ids=tool_call_ids,
                termination_reason="budget_exhausted",
                error="Agent loop cost budget exhausted",
            )
        response = gateway.complete(
            ModelRequest(
                request_id=f"loop-{uuid.uuid4().hex[:10]}",
                run_id=run_id,
                task_id=task_id,
                session_id=session_id,
                model_profile=model_profile,
                messages=history,
                tools=tools,
                max_output_tokens=max(
                    1, min(6000, max_output_tokens - usage.output_tokens)
                ),
                temperature=0.1,
                timeout_seconds=min(timeout_seconds, 120),
                seed=seed,
                max_cost_usd=float(remaining),
            )
        )
        usage = usage.merge(response.usage)
        if (
            response.usage.input_tokens > max_input_tokens
            or usage.output_tokens > max_output_tokens
        ):
            return AgentLoopResult(
                status="failed",
                usage=usage,
                rounds=round_no,
                tool_call_ids=tool_call_ids,
                termination_reason="token_budget_exhausted",
                error="Agent loop token budget exhausted",
            )
        if response.status != "tool_calls" or not response.tool_calls:
            if response.status == "success" and (response.text or "").strip():
                return AgentLoopResult(
                    status="success",
                    final_text=response.text or "",
                    usage=usage,
                    rounds=round_no,
                    tool_call_ids=tool_call_ids,
                    termination_reason="model_finished",
                )
            return AgentLoopResult(
                status="failed",
                final_text=response.text or "",
                usage=usage,
                rounds=round_no,
                tool_call_ids=tool_call_ids,
                termination_reason=f"model_{response.status}",
                error=f"Model finished with status {response.status}",
            )

        history.append(
            CanonicalMessage(
                role="assistant",
                content=response.text or "",
                tool_calls=response.tool_calls,
            )
        )
        for call in response.tool_calls:
            signature = hashlib.sha256(
                json.dumps(
                    {"name": call.name, "arguments": call.arguments},
                    sort_keys=True,
                    default=str,
                ).encode()
            ).hexdigest()
            seen_calls[signature] = seen_calls.get(signature, 0) + 1
            if seen_calls[signature] > 2:
                return AgentLoopResult(
                    status="failed",
                    usage=usage,
                    rounds=round_no,
                    tool_call_ids=tool_call_ids,
                    termination_reason="no_progress",
                    error=f"Repeated identical tool call: {call.name}",
                )
            write_fp = _write_fingerprint(call.name, call.arguments)
            if write_fp is not None and inspected_repository:
                seen_patch_fps[write_fp] = seen_patch_fps.get(write_fp, 0) + 1
                if seen_patch_fps[write_fp] > 2:
                    return AgentLoopResult(
                        status="failed",
                        usage=usage,
                        rounds=round_no,
                        tool_call_ids=tool_call_ids,
                        termination_reason="no_progress",
                        error="Repeated identical patch fingerprint",
                    )
            if len(tool_call_ids) >= max_tool_calls:
                return AgentLoopResult(
                    status="failed",
                    usage=usage,
                    rounds=round_no,
                    tool_call_ids=tool_call_ids,
                    termination_reason="tool_budget_exhausted",
                    error="Tool-call budget exhausted",
                )
            if call.name in {"create_file", "apply_patch"} and not inspected_repository:
                history.append(
                    CanonicalMessage(
                        role="tool",
                        content=json.dumps(
                            {
                                "error": "Inspect the repository with list_files, read_file, "
                                "or search_text before writing."
                            }
                        ),
                        name=call.name,
                        tool_call_id=call.id,
                    )
                )
                continue
            try:
                result = broker.execute(
                    task_id=task_id,
                    tool_name=call.name,
                    arguments=call.arguments,
                )
                if result.get("tool_call_id"):
                    tool_call_ids.append(str(result["tool_call_id"]))
                content = json.dumps(result, default=str)
                if call.name in {"list_files", "read_file", "search_text"}:
                    inspected_repository = True
                if call.name == "run_validation_command":
                    unregistered_command_failures = 0
            except Exception as exc:  # return execution failure to the model
                content = json.dumps({"error": str(exc), "tool": call.name})
                if call.name == "run_validation_command" and "Unregistered command" in str(
                    exc
                ):
                    unregistered_command_failures += 1
                    if unregistered_command_failures >= 2:
                        history.append(
                            CanonicalMessage(
                                role="tool",
                                content=content,
                                name=call.name,
                                tool_call_id=call.id,
                            )
                        )
                        return AgentLoopResult(
                            status="failed",
                            usage=usage,
                            rounds=round_no,
                            tool_call_ids=tool_call_ids,
                            termination_reason="no_progress",
                            error=(
                                "Repeated unregistered validation command ids; "
                                "use a registered policy command_id"
                            ),
                        )
            if len(content) > 16_000:
                digest = hashlib.sha256(content.encode()).hexdigest()
                content = content[:16_000] + f"...<truncated sha256={digest}>"
            history.append(
                CanonicalMessage(
                    role="tool",
                    content=content,
                    name=call.name,
                    tool_call_id=call.id,
                )
            )

    return AgentLoopResult(
        status="failed",
        usage=usage,
        rounds=max_rounds,
        tool_call_ids=tool_call_ids,
        termination_reason="max_rounds",
        error="Agent loop reached max rounds",
    )
