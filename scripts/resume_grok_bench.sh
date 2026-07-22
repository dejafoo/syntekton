#!/usr/bin/env bash
# Detached resume of the interrupted Grok-judge full bench.
# Requires OPENROUTER_API_KEY in the environment. Does not print the key.
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is not set. Export it in this shell, then re-run:" >&2
  echo "  export OPENROUTER_API_KEY=..." >&2
  echo "  ./scripts/resume_grok_bench.sh" >&2
  exit 2
fi
unset PRODUCT_FACTORY_FORCE_MOCK || true
export PYTHONUNBUFFERED=1
LOG=".product-factory/bench_resume.log"
OUT=".product-factory/bench_resume.out"
: >"$LOG"
: >"$OUT"
echo "Starting resume at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$OUT"
nohup uv run product-factory bench run \
  --live \
  --judge grok_judge \
  --subjects full_orchestration,single_agent_baseline \
  --limit 30 \
  --oracle-budget-usd 10.00 \
  --resume bench-fc9f2aa05c0c \
  --progress-log "$LOG" \
  >>"$OUT" 2>&1 &
echo $! > .product-factory/bench_resume.pid
echo "PID=$(cat .product-factory/bench_resume.pid)"
echo "Progress: tail -f $LOG"
echo "Output:   tail -f $OUT"
