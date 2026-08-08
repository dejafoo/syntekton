#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv python pin 3.13
# Prefer frozen installs when uv.lock is present (SD5). Fall back only for
# lock-less checkouts so bootstrap remains usable mid-migration.
if [[ -f uv.lock ]]; then
  uv sync --frozen --extra dev
else
  echo "warning: uv.lock missing; resolving from pyproject.toml" >&2
  uv sync --extra dev
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required to build the bundled observability dashboard" >&2
  exit 1
fi
npm --prefix dashboard ci
npm --prefix dashboard run build
echo "Bootstrap complete. Activate with: source .venv/bin/activate"
