#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv python pin 3.13
uv sync --extra dev
echo "Bootstrap complete. Activate with: source .venv/bin/activate"
