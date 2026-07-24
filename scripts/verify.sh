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
# Optional OpenCode plugin smoke (P3.G.D): skips when `opencode` is absent
# unless OPENCODE_INTEGRATION=1 is set (then missing binary fails).
bash scripts/opencode_plugin_smoke.sh
echo "verify OK"
