"""Unit tests for MCP tool handlers against a mock HostService."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from product_factory.host.protocol import HOST_PROTOCOL, HostResponse, HostSubscription
from product_factory.host_mcp.server import McpServer, _read_message, _write_message
from product_factory.host_mcp.tools import TOOL_NAMES, dispatch_tool, tool_schemas


def _ok(**kwargs: Any) -> HostResponse:
    return HostResponse.success(**kwargs)


def test_tool_schemas_match_small_tool_set() -> None:
    names = [t["name"] for t in tool_schemas()]
    assert names == list(TOOL_NAMES)
    assert len(names) == 8


def test_dispatch_unknown_tool() -> None:
    service = MagicMock()
    payload = dispatch_tool(service, "pf_nope", {})
    assert payload["ok"] is False
    assert payload["protocol"] == HOST_PROTOCOL
    assert payload["error"]["code"] == "unknown_tool"
    service.assert_not_called()


def test_pf_submit_builds_request_and_returns_host_response() -> None:
    service = MagicMock()
    service.submit.return_value = _ok(
        run_id="run-abc",
        status="queued",
        subscription=HostSubscription(cli_tail="product-factory host tail run-abc"),
    )
    payload = dispatch_tool(
        service,
        "pf_submit",
        {
            "request_text": "Investigate validation selection",
            "workflow": "repository_investigation",
            "repository_path": "/tmp/repo",
            "budget_usd": 1.5,
            "validation_commands": ["unit"],
            "mock": True,
        },
    )
    assert payload["ok"] is True
    assert payload["run_id"] == "run-abc"
    assert payload["status"] == "queued"
    service.submit.assert_called_once()
    request = service.submit.call_args.args[0]
    assert request.workflow_type == "repository_investigation"
    assert request.request_text.startswith("Investigate")
    assert request.validation_commands == ["unit"]
    assert float(request.budget.max_cost_usd) == 1.5
    kwargs = service.submit.call_args.kwargs
    assert kwargs["mock"] is True
    assert kwargs["detach"] is True


def test_pf_submit_rejects_blank_request() -> None:
    service = MagicMock()
    payload = dispatch_tool(service, "pf_submit", {"request_text": "  "})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "invalid_arguments"
    service.submit.assert_not_called()


def test_pf_status_inspect_approve_reject_cancel_export() -> None:
    service = MagicMock()
    service.status.return_value = _ok(run_id="run-1", status="executing")
    service.inspect.return_value = _ok(run_id="run-1", status="awaiting_approval")
    service.approve.return_value = _ok(run_id="run-1", status="completed")
    service.reject.return_value = _ok(run_id="run-1", status="blocked")
    service.cancel.return_value = _ok(run_id="run-1", status="cancelled")
    service.export_bundle.return_value = _ok(
        run_id="run-1",
        status="completed",
        data={"bundle_path": "/tmp/export.zip"},
    )

    assert dispatch_tool(service, "pf_status", {"run_id": "run-1"})["status"] == "executing"
    assert (
        dispatch_tool(service, "pf_inspect", {"run_id": "run-1"})["status"]
        == "awaiting_approval"
    )
    assert (
        dispatch_tool(service, "pf_approve", {"run_id": "run-1", "apply": True})["ok"]
        is True
    )
    service.approve.assert_called_with("run-1", apply=True)
    assert dispatch_tool(service, "pf_reject", {"run_id": "run-1"})["status"] == "blocked"
    assert dispatch_tool(service, "pf_cancel", {"run_id": "run-1"})["status"] == "cancelled"
    exported = dispatch_tool(service, "pf_export", {"run_id": "run-1"})
    assert exported["data"]["bundle_path"] == "/tmp/export.zip"
    service.export_bundle.assert_called_with("run-1", as_zip=True)


def test_pf_tail_returns_one_batch() -> None:
    service = MagicMock()
    service.tail.return_value = iter(
        [
            _ok(
                run_id="run-1",
                status="planning",
                events=[{"seq": 1, "type": "run.started"}],
                data={"after_seq": 1},
            )
        ]
    )
    payload = dispatch_tool(service, "pf_tail", {"run_id": "run-1", "after_seq": 0})
    assert payload["ok"] is True
    assert payload["events"][0]["seq"] == 1
    service.tail.assert_called_once()
    assert service.tail.call_args.kwargs["follow"] is False


def test_mcp_server_initialize_and_tools_call(tmp_path) -> None:
    service = MagicMock()
    service.status.return_value = _ok(run_id="run-z", status="queued")
    server = McpServer(service)

    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "product-factory"
    assert "tools" in init["result"]["capabilities"]

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    assert len(listed["result"]["tools"]) == 8

    called = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "pf_status", "arguments": {"run_id": "run-z"}},
        }
    )
    assert called is not None
    text = called["result"]["content"][0]["text"]
    body = json.loads(text)
    assert body["protocol"] == HOST_PROTOCOL
    assert body["run_id"] == "run-z"
    assert called["result"]["structuredContent"]["ok"] is True


def test_content_length_framing_roundtrip(tmp_path) -> None:
    path = tmp_path / "pipe.bin"
    with path.open("wb") as fh:
        _write_message(fh, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
    with path.open("rb") as fh:
        msg = _read_message(fh)
    assert msg == {"jsonrpc": "2.0", "id": 1, "method": "ping"}
