"""Product Factory MCP adapter for host CLIs (OpenCode, Cursor, …).

Thin stdio MCP server that calls :class:`~product_factory.host.service.HostService`
directly — no HTTP round-trips. Tools return ``product-factory.host/v1``
``HostResponse`` JSON.
"""

from product_factory.host_mcp.server import run_stdio
from product_factory.host_mcp.tools import TOOL_NAMES, dispatch_tool

__all__ = ["TOOL_NAMES", "dispatch_tool", "run_stdio"]
