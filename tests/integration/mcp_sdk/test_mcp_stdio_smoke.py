"""MCP stdio smoke — connect like StdioClientTransport, list tools.

Uses NDJSON framing (same wire format as the TypeScript MCP SDK's
``StdioClientTransport``) against ``product-factory mcp --mock``. Always-on
in CI; no npm/SDK package required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class _NdjsonStdioClient:
    """Minimal MCP client over NDJSON stdio (StdioClientTransport-compatible)."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self._proc = proc
        self._next_id = 1
        assert proc.stdin is not None
        assert proc.stdout is not None
        self._stdin = proc.stdin
        self._stdout = proc.stdout

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self._stdin.write(line)
        self._stdin.flush()
        raw = self._stdout.readline()
        if not raw:
            raise RuntimeError("MCP server closed stdout before responding")
        response = json.loads(raw.decode("utf-8"))
        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")
        return response

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        line = json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"
        self._stdin.write(line)
        self._stdin.flush()

    def close(self) -> None:
        if self._proc.stdin:
            self._proc.stdin.close()
        self._proc.wait(timeout=10)


def test_mcp_stdio_lists_pf_materialize(tmp_path: Path) -> None:
    root = _repo_root()
    data_dir = tmp_path / ".product-factory"
    data_dir.mkdir()
    env = os.environ.copy()
    env["PRODUCT_FACTORY_FORCE_MOCK"] = "1"
    env["PRODUCT_FACTORY_ROOT"] = str(root)
    cmd = [
        sys.executable,
        "-m",
        "product_factory",
        "mcp",
        "--mock",
        "--data-dir",
        str(data_dir),
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(tmp_path),
        env=env,
    )
    client = _NdjsonStdioClient(proc)
    try:
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pf-mcp-smoke", "version": "0"},
            },
        )
        assert init["result"]["serverInfo"]["name"] == "product-factory"
        client.notify("notifications/initialized")

        listed = client.request("tools/list")
        names = [t["name"] for t in listed["result"]["tools"]]
        assert "pf_submit" in names
        assert "pf_materialize" in names
        assert "pf_approve" in names
    finally:
        client.close()
        if proc.returncode not in (0, None) and proc.stderr:
            err = proc.stderr.read().decode("utf-8", errors="replace")
            if err.strip():
                print(err, file=sys.stderr)
