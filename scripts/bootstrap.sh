#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv python pin 3.13
uv sync --extra dev
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to build the bundled observability dashboard" >&2
  exit 1
fi
npm --prefix dashboard ci
npm --prefix dashboard run build
echo "Bootstrap complete. Activate with: source .venv/bin/activate"
