"""Minimal stdio MCP server (JSON-RPC + Content-Length framing).

Intentionally small: no official ``mcp`` SDK dependency. Tools call
:class:`~product_factory.host.service.HostService` directly.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from product_factory import __version__
from product_factory.host.service import HostService
from product_factory.host_mcp.factory import build_host_service
from product_factory.host_mcp.tools import dispatch_tool, tool_schemas

SERVER_NAME = "product-factory"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
    }
)


def _read_message(stdin: BinaryIO) -> dict[str, Any] | None:
    """Read one MCP message (Content-Length framed or newline-delimited JSON)."""
    headers: dict[str, str] = {}
    while True:
        line = stdin.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        # Newline-delimited JSON shortcut (no headers).
        stripped = line.strip()
        if stripped.startswith(b"{") and b":" in stripped and b"Content-Length" not in line:
            try:
                msg = json.loads(stripped.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                return msg
            return None
        try:
            text = line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError:
            return None
        if ":" not in text:
            continue
        key, value = text.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    length_raw = headers.get("content-length")
    if not length_raw:
        return None
    try:
        length = int(length_raw)
    except ValueError:
        return None
    body = stdin.read(length)
    if not body:
        return None
    try:
        msg = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


def _write_message(stdout: BinaryIO, message: Mapping[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
    stdout.write(header)
    stdout.write(payload)
    stdout.flush()


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


class McpServer:
    """JSON-RPC MCP server bound to a HostService."""

    def __init__(self, service: HostService) -> None:
        self.service = service
        self._initialized = False
        self._protocol_version = DEFAULT_PROTOCOL_VERSION

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one request; return a response or None for notifications."""
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # Notifications have no id.
        if msg_id is None:
            if method == "notifications/initialized":
                self._initialized = True
            return None

        if method == "initialize":
            client_version = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
            if client_version in SUPPORTED_PROTOCOL_VERSIONS:
                self._protocol_version = str(client_version)
            else:
                self._protocol_version = DEFAULT_PROTOCOL_VERSION
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": self._protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": __version__,
                    },
                    "instructions": (
                        "Product Factory host tools. Submit curated request text only "
                        "(no full chat dumps). Prefer pf_submit → pf_status/pf_tail → "
                        "pf_inspect → pf_approve|pf_reject. Tools return "
                        "product-factory.host/v1 HostResponse JSON."
                    ),
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": tool_schemas()},
            }

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32602, "message": "tools/call requires name"},
                }
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                payload = dispatch_tool(self.service, name, arguments)
            except Exception as exc:  # noqa: BLE001 — surface as tool error to host
                payload = {
                    "protocol": "product-factory.host/v1",
                    "ok": False,
                    "error": {
                        "code": exc.__class__.__name__,
                        "message": str(exc),
                        "details": {},
                    },
                }
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": _tool_result(payload, is_error=True),
                }
            is_error = not bool(payload.get("ok", True))
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": _tool_result(payload, is_error=is_error),
            }

        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def serve_stdio(
    service: HostService,
    *,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | None = None,
    stderr: TextIO | None = None,
) -> None:
    """Run the MCP loop until stdin EOF."""
    in_stream = stdin or sys.stdin.buffer
    out_stream = stdout or sys.stdout.buffer
    err = stderr or sys.stderr
    server = McpServer(service)
    while True:
        message = _read_message(in_stream)
        if message is None:
            break
        try:
            response = server.handle(message)
        except Exception as exc:  # noqa: BLE001
            err.write(f"product-factory mcp error: {exc}\n")
            err.flush()
            msg_id = message.get("id")
            if msg_id is not None:
                _write_message(
                    out_stream,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {"code": -32603, "message": str(exc)},
                    },
                )
            continue
        if response is not None:
            _write_message(out_stream, response)


def run_stdio(
    *,
    mock: bool = False,
    data_dir: Path | None = None,
    project_root: Path | None = None,
    service_factory: Callable[..., HostService] | None = None,
) -> None:
    """Entrypoint used by ``product-factory mcp``."""
    factory = service_factory or build_host_service
    service = factory(mock=mock, data_dir=data_dir, project_root=project_root)
    serve_stdio(service)
