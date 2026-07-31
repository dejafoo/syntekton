"""Minimal stdio MCP *client* for talking to a local MCP server subprocess.

Product Factory already exposes an MCP server for hosts like OpenCode
(`host_mcp/server.py`). This is the other direction: a worker connector acting as
a client to some third-party server. The two are unrelated, and this module
deliberately shares no state with the host side.

Framing is newline-delimited JSON, which is what the TypeScript MCP SDK emits.
Stdout is drained by a reader thread rather than read inline, because a blocking
`readline` on a hung server ignores any deadline the caller set: the timeout has
to be enforced by whoever is waiting, not by the pipe. A server that writes noise
to stdout also does not corrupt the stream — unparseable lines are skipped rather
than treated as a reply.

Nothing a server says is authoritative. Its advertised tool list is only used to
resolve names the connector already allowlists.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from product_factory.connectors.errors import ConnectorTimeout, ConnectorUnavailable

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "product-factory-connector"

# A single line longer than this means the server is misbehaving; stop reading
# rather than buffering an unbounded response into memory.
MAX_LINE_BYTES = 8 * 1024 * 1024


class McpStdioClient:
    """One MCP server subprocess, spoken to over stdio.

    Not thread-safe by itself; the connector broker's per-connector concurrency
    limit plus this class's own lock serialize request/response pairs.
    """

    def __init__(
        self,
        *,
        command: str,
        args: Sequence[str] = (),
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 30.0,
        connector_id: str = "",
    ) -> None:
        self.command = command
        self.args = tuple(args)
        self.cwd = cwd
        self.env = dict(env) if env is not None else None
        self.timeout_seconds = timeout_seconds
        self.connector_id = connector_id
        self._process: subprocess.Popen[bytes] | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self._server_tools: tuple[str, ...] = ()
        self._lines: queue.Queue[bytes | None] = queue.Queue()
        self._reader: threading.Thread | None = None

    @property
    def server_tools(self) -> tuple[str, ...]:
        """Tool names the server advertised. Informational only, never a grant."""
        return self._server_tools

    def _unavailable(self, message: str, **details: Any) -> ConnectorUnavailable:
        return ConnectorUnavailable(
            message, connector_id=self.connector_id, details=details or None
        )

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        try:
            self._process = subprocess.Popen(  # noqa: S603 - command comes from operator config
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                env=self.env if self.env is not None else os.environ.copy(),
            )
        except FileNotFoundError as exc:
            raise self._unavailable(
                f"MCP server command not found: {self.command}", command=self.command
            ) from exc
        except OSError as exc:
            raise self._unavailable(
                f"Could not start MCP server {self.command}: {type(exc).__name__}",
                command=self.command,
            ) from exc

        self._lines = queue.Queue()
        self._reader = threading.Thread(
            target=self._drain_stdout,
            args=(self._process, self._lines),
            name=f"mcp-reader-{self.connector_id or self.command}",
            daemon=True,
        )
        self._reader.start()
        self._handshake()

    @staticmethod
    def _drain_stdout(process: subprocess.Popen[bytes], lines: queue.Queue[bytes | None]) -> None:
        """Move stdout lines into a queue so waiters can apply their own timeout."""
        stdout = process.stdout
        if stdout is None:
            lines.put(None)
            return
        try:
            for line in stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            # Sentinel: the server will send nothing further.
            lines.put(None)

    def _handshake(self) -> None:
        response = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": "1.0.0"},
            },
        )
        if not isinstance(response, dict):
            raise self._unavailable("MCP server returned a malformed initialize result")
        self._notify("notifications/initialized", {})
        listed = self._request("tools/list", {})
        tools = listed.get("tools") if isinstance(listed, dict) else None
        names: list[str] = []
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    names.append(tool["name"])
        self._server_tools = tuple(names)

    def resolve_tool_name(self, candidates: Sequence[str]) -> str:
        """First advertised name among `candidates`.

        Servers rename tools between versions (`read_file` became
        `read_text_file`), so the connector carries a candidate list rather than
        pinning one name. Candidates are still a fixed allowlist — a name the
        server invents is never callable.
        """
        advertised = set(self._server_tools)
        for candidate in candidates:
            if candidate in advertised:
                return candidate
        # No tools/list response at all: trust the first candidate and let the
        # call fail typed if it is wrong.
        if not advertised:
            return candidates[0]
        raise self._unavailable(
            f"MCP server advertises none of {list(candidates)}",
            advertised=sorted(advertised),
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise self._unavailable(f"MCP tool {name} returned a malformed result")
        if result.get("isError"):
            raise self._unavailable(
                f"MCP tool {name} reported an error: {_text_of(result)[:500]}",
                tool=name,
            )
        return result

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            message = self._read_until_id(request_id)
        error = message.get("error")
        if error is not None:
            detail = error.get("message") if isinstance(error, dict) else str(error)
            raise self._unavailable(f"MCP {method} failed: {detail}", method=method)
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise self._unavailable("MCP server is not running")
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            process.stdin.write(payload + b"\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._unavailable(f"MCP server closed its input: {type(exc).__name__}") from exc

    def _read_until_id(self, request_id: int) -> dict[str, Any]:
        """Read replies until the one matching `request_id`.

        Notifications and log lines arrive interleaved with replies, so anything
        that is not the awaited response is discarded.
        """
        process = self._process
        if process is None or process.stdout is None:
            raise self._unavailable("MCP server is not running")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConnectorTimeout(
                    f"MCP server did not answer within {self.timeout_seconds}s",
                    connector_id=self.connector_id,
                )
            try:
                line = self._lines.get(timeout=remaining)
            except queue.Empty:
                raise ConnectorTimeout(
                    f"MCP server did not answer within {self.timeout_seconds}s",
                    connector_id=self.connector_id,
                ) from None
            if line is None:
                raise self._unavailable(
                    "MCP server exited without answering",
                    exit_code=process.poll(),
                    stderr=self._drain_stderr(process),
                )
            if len(line) > MAX_LINE_BYTES:
                raise self._unavailable(
                    "MCP server sent an oversized line", max_line_bytes=MAX_LINE_BYTES
                )
            stripped = line.strip()
            if not stripped.startswith(b"{"):
                # Servers that log to stdout would otherwise corrupt the stream.
                continue
            try:
                message = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("id") == request_id:
                return message

    @staticmethod
    def _drain_stderr(process: subprocess.Popen[bytes]) -> str:
        """Best-effort stderr tail, to explain why a server died."""
        if process.stderr is None:
            return ""
        try:
            return (process.stderr.read() or b"").decode("utf-8", "replace")[:1_000]
        except (OSError, ValueError):
            return ""

    def close(self) -> None:
        process = self._process
        reader = self._reader
        self._process = None
        self._reader = None
        if process is None:
            return
        # Closing stdin first lets a well-behaved server exit on its own.
        if process.stdin is not None:
            with contextlib.suppress(OSError):
                process.stdin.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=2)
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    def __enter__(self) -> McpStdioClient:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _text_of(result: Mapping[str, Any]) -> str:
    """Concatenate the text blocks of an MCP tool result."""
    content = result.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def text_of(result: Mapping[str, Any]) -> str:
    return _text_of(result)


__all__ = ["MAX_LINE_BYTES", "PROTOCOL_VERSION", "McpStdioClient", "text_of"]
