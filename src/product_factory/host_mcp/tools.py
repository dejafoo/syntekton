"""MCP tool handlers — call HostService directly; return HostResponse JSON."""

from __future__ import annotations

import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from product_factory.domain.budgets import RunBudget
from product_factory.domain.runs import RunRequest
from product_factory.host.protocol import HostResponse
from product_factory.host.service import HostService

TOOL_NAMES = (
    "pf_submit",
    "pf_status",
    "pf_tail",
    "pf_inspect",
    "pf_approve",
    "pf_reject",
    "pf_cancel",
    "pf_export",
)

_WORKFLOW_VALUES = {
    "architecture",
    "technical_plan",
    "code_change",
    "repository_change",
    "repository_investigation",
}


def _as_json(response: HostResponse) -> dict[str, Any]:
    return response.model_dump(mode="json")


def _failure(code: str, message: str, **kwargs: Any) -> dict[str, Any]:
    return _as_json(HostResponse.failure(code=code, message=message, **kwargs))


def tool_schemas() -> list[dict[str, Any]]:
    """JSON Schema definitions for MCP ``tools/list``."""
    return [
        {
            "name": "pf_submit",
            "description": (
                "Submit a curated Product Factory request. Returns HostResponse "
                "with run_id + subscription. Do not dump full chat transcripts — "
                "pass named request text only."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_text": {
                        "type": "string",
                        "description": "Curated operator request text",
                    },
                    "workflow": {
                        "type": "string",
                        "description": (
                            "Workflow pack id: repository_investigation, "
                            "technical_plan, repository_change, code_change, architecture"
                        ),
                        "default": "code_change",
                    },
                    "repository_path": {
                        "type": "string",
                        "description": "Absolute or cwd-relative repo path",
                    },
                    "budget_usd": {
                        "type": "number",
                        "description": "Max USD budget for the run",
                        "default": 3.0,
                    },
                    "validation_commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Validation command ids to require",
                    },
                    "mock": {
                        "type": "boolean",
                        "description": "Force mock gateway",
                        "default": False,
                    },
                    "profile": {
                        "type": "string",
                        "description": "Model profile set",
                        "default": "local-target",
                    },
                },
                "required": ["request_text"],
            },
        },
        {
            "name": "pf_status",
            "description": "Get HostResponse status for a run_id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_tail",
            "description": (
                "Fetch one event batch after after_seq (non-blocking). "
                "Poll until terminal or awaiting_approval."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "after_seq": {
                        "type": "integer",
                        "description": "Resume cursor (inclusive lower bound)",
                        "default": 0,
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_inspect",
            "description": "Inspect plan, validations, artifacts, and approval record.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_approve",
            "description": "Approve a run awaiting operator approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "apply": {
                        "type": "boolean",
                        "description": "Apply patch to working tree when true",
                        "default": False,
                    },
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_reject",
            "description": "Reject a run awaiting operator approval.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_cancel",
            "description": "Request cooperative cancel for a run.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                },
                "required": ["run_id"],
            },
        },
        {
            "name": "pf_export",
            "description": "Export a redaction-aware evidence bundle; path in data.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "as_zip": {
                        "type": "boolean",
                        "default": True,
                    },
                },
                "required": ["run_id"],
            },
        },
    ]


def pf_submit(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    request_text = arguments.get("request_text")
    if not isinstance(request_text, str) or not request_text.strip():
        return _failure("invalid_arguments", "request_text is required")

    workflow = arguments.get("workflow") or "code_change"
    if workflow not in _WORKFLOW_VALUES:
        return _failure(
            "invalid_arguments",
            f"Unknown workflow {workflow!r}",
            details={"allowed": sorted(_WORKFLOW_VALUES)},
        )

    repo_raw = arguments.get("repository_path")
    repository_path: Path | None = None
    if repo_raw:
        repository_path = Path(str(repo_raw)).expanduser().resolve()

    budget_usd = float(arguments.get("budget_usd") if arguments.get("budget_usd") is not None else 3.0)
    validation_commands = arguments.get("validation_commands") or []
    if not isinstance(validation_commands, list):
        return _failure("invalid_arguments", "validation_commands must be a list of strings")
    mock = bool(arguments.get("mock", False))
    profile = str(arguments.get("profile") or "local-target")

    run_request = RunRequest(
        request_id=f"req-{uuid.uuid4().hex[:8]}",
        workflow_type=workflow,  # type: ignore[arg-type]
        request_text=request_text,
        repository_path=repository_path,
        model_profile_set=profile,
        validation_commands=[str(c) for c in validation_commands],
        budget=RunBudget(max_cost_usd=Decimal(str(budget_usd))),
    )
    return _as_json(
        service.submit(
            run_request,
            mock=mock,
            detach=True,
            inline_thread=False,
        )
    )


def pf_status(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    return _as_json(service.status(run_id))


def pf_tail(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    after_seq = int(arguments.get("after_seq") or 0)
    batch = next(
        service.tail(
            run_id,
            after_seq=after_seq,
            follow=False,
            max_idle_polls=1,
            stop_when_terminal=True,
        ),
        None,
    )
    if batch is None:
        return _failure("not_found", f"No events for {run_id}", run_id=run_id)
    return _as_json(batch)


def pf_inspect(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    return _as_json(service.inspect(run_id))


def pf_approve(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    apply = bool(arguments.get("apply", False))
    return _as_json(service.approve(run_id, apply=apply))


def pf_reject(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    return _as_json(service.reject(run_id))


def pf_cancel(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    return _as_json(service.cancel(run_id))


def pf_export(service: HostService, arguments: dict[str, Any]) -> dict[str, Any]:
    run_id = arguments.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return _failure("invalid_arguments", "run_id is required")
    as_zip = bool(arguments.get("as_zip", True))
    return _as_json(service.export_bundle(run_id, as_zip=as_zip))


_HANDLERS = {
    "pf_submit": pf_submit,
    "pf_status": pf_status,
    "pf_tail": pf_tail,
    "pf_inspect": pf_inspect,
    "pf_approve": pf_approve,
    "pf_reject": pf_reject,
    "pf_cancel": pf_cancel,
    "pf_export": pf_export,
}


def dispatch_tool(
    service: HostService,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch a tool by name; always returns HostResponse-shaped JSON."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return _failure("unknown_tool", f"Unknown tool {name!r}")
    return handler(service, arguments or {})
