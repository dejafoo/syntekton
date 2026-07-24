"""Unit tests for MCP tool handlers against a mock HostService."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from product_factory.host.protocol import HOST_PROTOCOL, HostResponse, HostSubscription
from product_factory.host_mcp.factory import resolve_mcp_config_root
from product_factory.host_mcp.server import McpServer, _read_message, _write_message
from product_factory.host_mcp.tools import TOOL_NAMES, dispatch_tool, tool_schemas


def _ok(**kwargs: Any) -> HostResponse:
    return HostResponse.success(**kwargs)


def test_tool_schemas_match_small_tool_set() -> None:
    names = [t["name"] for t in tool_schemas()]
    assert names == list(TOOL_NAMES)
    assert len(names) == 9
    assert "pf_materialize" in names


def test_resolve_mcp_config_root_falls_back_to_package(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PRODUCT_FACTORY_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    root = resolve_mcp_config_root()
    assert (root / "config" / "models.yaml").exists()


def test_resolve_mcp_config_root_respects_env(tmp_path, monkeypatch) -> None:
    cfg = tmp_path / "pf"
    (cfg / "config").mkdir(parents=True)
    (cfg / "config" / "models.yaml").write_text("profiles: {}\n", encoding="utf-8")
    monkeypatch.setenv("PRODUCT_FACTORY_ROOT", str(cfg))
    monkeypatch.chdir(tmp_path)
    assert resolve_mcp_config_root() == cfg.resolve()


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
    assert len(listed["result"]["tools"]) == 9

    prompts = server.handle({"jsonrpc": "2.0", "id": 20, "method": "prompts/list"})
    assert prompts is not None
    assert prompts["result"]["prompts"] == []
    resources = server.handle({"jsonrpc": "2.0", "id": 21, "method": "resources/list"})
    assert resources is not None
    assert resources["result"]["resources"] == []

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
        _write_message(
            fh,
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            framing="content-length",
        )
    with path.open("rb") as fh:
        msg, framing = _read_message(fh)
    assert framing == "content-length"
    assert msg == {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_ndjson_framing_roundtrip(tmp_path) -> None:
    path = tmp_path / "pipe.ndjson"
    with path.open("wb") as fh:
        _write_message(
            fh,
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            framing="ndjson",
        )
    with path.open("rb") as fh:
        msg, framing = _read_message(fh)
    assert framing == "ndjson"
    assert msg == {"jsonrpc": "2.0", "id": 1, "method": "ping"}


def test_negotiate_protocol_version_echoes_modern_client() -> None:
    service = MagicMock()
    server = McpServer(service)
    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"roots": {}},
                "clientInfo": {"name": "opencode", "version": "1.18.4"},
            },
        }
    )
    assert init is not None
    assert init["result"]["protocolVersion"] == "2025-11-25"


def test_pf_materialize_dispatches_to_host_service() -> None:
    service = MagicMock()
    service.materialize.return_value = _ok(
        run_id="run-1",
        status="awaiting_approval",
        data={
            "written_path": "/repo/docs/ARCHITECTURE.md",
            "artifact": {"logical_name": "ARCHITECTURE.md", "sha256": "abc"},
        },
    )
    payload = dispatch_tool(
        service,
        "pf_materialize",
        {
            "run_id": "run-1",
            "artifact": "ARCHITECTURE.md",
            "dest_path": "docs/ARCHITECTURE.md",
            "overwrite": True,
        },
    )
    assert payload["ok"] is True
    assert payload["data"]["written_path"].endswith("ARCHITECTURE.md")
    service.materialize.assert_called_once_with(
        "run-1",
        artifact="ARCHITECTURE.md",
        dest_path="docs/ARCHITECTURE.md",
        overwrite=True,
    )


def test_pf_materialize_requires_args() -> None:
    service = MagicMock()
    missing = dispatch_tool(service, "pf_materialize", {"run_id": "run-1"})
    assert missing["ok"] is False
    assert missing["error"]["code"] == "invalid_arguments"
    service.materialize.assert_not_called()


def test_mcp_server_lazy_host_service_init() -> None:
    factory = MagicMock()
    service = MagicMock()
    service.status.return_value = _ok(run_id="run-z", status="queued")
    factory.return_value = service

    server = McpServer(service_factory=factory)
    init = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
        }
    )
    assert init is not None
    factory.assert_not_called()

    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    factory.assert_not_called()

    server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "pf_status", "arguments": {"run_id": "run-z"}},
        }
    )
    factory.assert_called_once()
