#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
npm --prefix dashboard run check
npm --prefix dashboard run build
uv run ruff format --check src tests
uv run ruff check src tests
uv run basedpyright
uv run pytest -q -m "not integration"
uv run product-factory --help

# Connector policy, injection, and grant harness (P4.B–D). Covered by the run
# above; kept explicit so a regression here is attributed to connectors rather
# than buried in the full suite. Every case is offline — connectors are disabled
# by default and the providers run in mock mode.
uv run pytest -q tests/connectors tests/contract/test_connector_audit.py

# Live connector smokes are opt-in: they need credentials or network egress, so
# they stay out of the default gate and fail loudly when explicitly requested.
if [[ "${TAVILY_INTEGRATION:-}" == "1" ]]; then
  uv run pytest -q tests/connectors/test_tavily_connector.py -k live_search
fi
if [[ "${MCP_FILESYSTEM_INTEGRATION:-}" == "1" ]]; then
  uv run pytest -q tests/connectors/test_filesystem_mcp.py -k real_filesystem
fi

# Optional OpenCode plugin smoke (P3.G.D): skips when `opencode` is absent
# unless OPENCODE_INTEGRATION=1 is set (then missing binary fails).
bash scripts/opencode_plugin_smoke.sh
echo "verify OK"
