#!/usr/bin/env bash
# Tier-2 OpenCode reality smoke for @product-factory/opencode-plugin (P3.G.D).
#
# Default: skip cleanly (exit 0) when `opencode` is not on PATH.
# OPENCODE_INTEGRATION=1: fail if `opencode` is missing.
#
# Does not call a live model / OpenRouter. Asserts that OpenCode 1.x loads the
# plugin and exposes pf_* tools via `opencode serve` + GET /experimental/tool/ids.
# Optionally also drives a mock host technical_plan → materialize into the temp
# project (same CLI path the plugin uses) when PRODUCT_FACTORY_BIN is usable.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_DIR="${REPO_ROOT}/integrations/opencode-plugin"
REQUIRED_TOOLS=(pf_run pf_wait pf_review pf_merge pf_decline)
FORCE_INTEGRATION="${OPENCODE_INTEGRATION:-0}"

log() { printf 'opencode-plugin-smoke: %s\n' "$*"; }
die() { log "FAIL: $*"; exit 1; }

# --- gate -------------------------------------------------------------------
if ! command -v opencode >/dev/null 2>&1; then
  if [[ "${FORCE_INTEGRATION}" == "1" || "${FORCE_INTEGRATION}" == "true" ]]; then
    die "opencode not on PATH (OPENCODE_INTEGRATION=1 requires it)"
  fi
  log "SKIP: opencode not on PATH (set OPENCODE_INTEGRATION=1 to require it)"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  die "curl is required for the OpenCode serve health/tool probe"
fi
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required to parse OpenCode JSON responses"
fi

OPENCODE_VERSION="$(opencode --version 2>/dev/null | head -n 1 | tr -d '\r')"
log "opencode --version: ${OPENCODE_VERSION}"

PF_BIN="${PRODUCT_FACTORY_BIN:-${REPO_ROOT}/.venv/bin/product-factory}"
if [[ ! -x "${PF_BIN}" ]]; then
  if command -v product-factory >/dev/null 2>&1; then
    PF_BIN="$(command -v product-factory)"
  else
    die "product-factory binary not found (set PRODUCT_FACTORY_BIN or create ${REPO_ROOT}/.venv)"
  fi
fi
log "PRODUCT_FACTORY_BIN=${PF_BIN}"

# --- temp project -----------------------------------------------------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/opencode-plugin-smoke.XXXXXX")"
cleanup() {
  if [[ -n "${SERVE_PID:-}" ]] && kill -0 "${SERVE_PID}" 2>/dev/null; then
    kill "${SERVE_PID}" 2>/dev/null || true
    wait "${SERVE_PID}" 2>/dev/null || true
  fi
  if [[ "${OPENCODE_SMOKE_KEEP:-0}" == "1" ]]; then
    log "keeping temp dir: ${TMP}"
  else
    rm -rf "${TMP}"
  fi
}
trap cleanup EXIT

HOME_ISO="${TMP}/home"
PROJ="${TMP}/proj"
DATA_DIR="${PROJ}/.product-factory"
mkdir -p \
  "${HOME_ISO}/.config/opencode" \
  "${HOME_ISO}/.local/share/opencode/log" \
  "${HOME_ISO}/.cache/opencode" \
  "${HOME_ISO}/.local/state" \
  "${PROJ}/.opencode/plugins" \
  "${DATA_DIR}"

# Isolate from the developer's ~/.config/opencode (MCP, other plugins).
export HOME="${HOME_ISO}"
export XDG_CONFIG_HOME="${HOME_ISO}/.config"
export XDG_DATA_HOME="${HOME_ISO}/.local/share"
export XDG_CACHE_HOME="${HOME_ISO}/.cache"
export XDG_STATE_HOME="${HOME_ISO}/.local/state"
export OPENCODE_DISABLE_AUTOUPDATE=1
export OPENCODE_DISABLE_MODELS_FETCH=1
export PRODUCT_FACTORY_FORCE_MOCK=1
export PRODUCT_FACTORY_BIN="${PF_BIN}"
export PRODUCT_FACTORY_ROOT="${REPO_ROOT}"
export PRODUCT_FACTORY_DATA_DIR="${DATA_DIR}"

# Proven load path on OpenCode 1.18.x: local plugin under .opencode/plugins with
# a file: dependency installed into .opencode/node_modules (Bun at serve start).
# A bare `plugin: ["file:…"]` entry alone did not register tools in smoke probes.
cat > "${PROJ}/.opencode/package.json" <<EOF
{
  "dependencies": {
    "@product-factory/opencode-plugin": "file:${PLUGIN_DIR}",
    "@opencode-ai/plugin": "1.18.4"
  }
}
EOF
cat > "${PROJ}/.opencode/plugins/product-factory.ts" <<'EOF'
export { default, ProductFactoryPlugin } from "@product-factory/opencode-plugin";
EOF
cat > "${PROJ}/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "plugin": ["file:${PLUGIN_DIR}"]
}
EOF
printf 'OpenCode plugin smoke fixture\n' > "${PROJ}/README.md"
# Ignore OpenCode's bun install tree and PF data so the repo stays clean for
# snapshot_repository (untracked .opencode/node_modules would otherwise fail).
cat > "${PROJ}/.gitignore" <<'EOF'
.opencode/
.product-factory/
EOF
# Host runs require a git repository (worktree / base commit).
git -C "${PROJ}" init -q
git -C "${PROJ}" config user.email "smoke@product-factory.local"
git -C "${PROJ}" config user.name "OpenCode Plugin Smoke"
git -C "${PROJ}" add README.md opencode.json .gitignore
git -C "${PROJ}" commit -q -m "smoke fixture"

log "temp project: ${PROJ}"

# --- serve + tool visibility ------------------------------------------------
# Prefer a free high port; fall back if busy.
PORT="${OPENCODE_SMOKE_PORT:-$((18000 + RANDOM % 2000))}"
SERVE_LOG="${TMP}/serve.log"
(
  cd "${PROJ}"
  exec opencode serve --port "${PORT}" --hostname 127.0.0.1 --print-logs --log-level INFO
) >"${SERVE_LOG}" 2>&1 &
SERVE_PID=$!

HEALTH_URL="http://127.0.0.1:${PORT}/global/health"
TOOLS_URL="http://127.0.0.1:${PORT}/experimental/tool/ids"
ready=0
for _ in $(seq 1 90); do
  if curl -sf "${HEALTH_URL}" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "${SERVE_PID}" 2>/dev/null; then
    tail -n 80 "${SERVE_LOG}" || true
    die "opencode serve exited before becoming healthy (log: ${SERVE_LOG})"
  fi
  sleep 1
done
[[ "${ready}" == "1" ]] || die "opencode serve did not become healthy within 90s"

HEALTH_JSON="$(curl -sf "${HEALTH_URL}")"
log "health: ${HEALTH_JSON}"

# Tool registration can lag health while Bun installs the local plugin.
TOOL_IDS_JSON=""
for _ in $(seq 1 60); do
  TOOL_IDS_JSON="$(curl -sf --max-time 5 "${TOOLS_URL}" || true)"
  if [[ -n "${TOOL_IDS_JSON}" ]]; then
    MISSING="$(
      TOOL_IDS_JSON="${TOOL_IDS_JSON}" REQUIRED="$(printf '%s\n' "${REQUIRED_TOOLS[@]}")" python3 - <<'PY'
import json, os
ids = set(json.loads(os.environ["TOOL_IDS_JSON"]))
required = [line for line in os.environ["REQUIRED"].splitlines() if line]
missing = [name for name in required if name not in ids]
print("\n".join(missing))
PY
    )"
    if [[ -z "${MISSING}" ]]; then
      break
    fi
  fi
  sleep 1
done
if [[ -z "${TOOL_IDS_JSON}" ]]; then
  tail -n 80 "${SERVE_LOG}" || true
  die "failed to fetch ${TOOLS_URL}"
fi
MISSING="$(
  TOOL_IDS_JSON="${TOOL_IDS_JSON}" REQUIRED="$(printf '%s\n' "${REQUIRED_TOOLS[@]}")" python3 - <<'PY'
import json, os
ids = set(json.loads(os.environ["TOOL_IDS_JSON"]))
required = [line for line in os.environ["REQUIRED"].splitlines() if line]
missing = [name for name in required if name not in ids]
print("\n".join(missing))
PY
)"
if [[ -n "${MISSING}" ]]; then
  log "tool ids: ${TOOL_IDS_JSON}"
  tail -n 80 "${SERVE_LOG}" || true
  die "plugin tools missing from OpenCode: $(echo "${MISSING}" | tr '\n' ' ')"
fi
log "plugin tools visible: ${REQUIRED_TOOLS[*]}"

# --- optional mock materialize (same CLI path the plugin uses; no LLM) ------
ARCH_PATH="${PROJ}/docs/ARCHITECTURE.md"
REQ_FILE="${TMP}/request.md"
cat > "${REQ_FILE}" <<'EOF'
Draft a short architecture overview for this fixture repository.
EOF

# Use --sync so the mock worker finishes in-process (avoids daemon-thread death
# on CLI exit and does not depend on a long-lived detached worker).
SUBMIT_JSON="$(
  cd "${PROJ}"
  "${PF_BIN}" host submit \
    --request "${REQ_FILE}" \
    --workflow technical_plan \
    --repo "${PROJ}" \
    --mock \
    --sync
)"
RUN_ID="$(printf '%s\n' "${SUBMIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["run_id"])')"
[[ -n "${RUN_ID}" && "${RUN_ID}" != "None" ]] || die "host submit did not return run_id: ${SUBMIT_JSON}"
STATUS="$(printf '%s\n' "${SUBMIT_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status") or "")')"
# --sync still returns the immediate "queued" envelope; re-read status.
STATUS_JSON="$("${PF_BIN}" host status "${RUN_ID}")"
STATUS="$(printf '%s\n' "${STATUS_JSON}" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("status") or "")')"
log "submitted mock technical_plan run_id=${RUN_ID} status=${STATUS}"
[[ "${STATUS}" == "awaiting_approval" || "${STATUS}" == "completed" ]] \
  || die "expected awaiting_approval/completed after --sync, got '${STATUS}'"

if [[ "${STATUS}" == "awaiting_approval" ]]; then
  "${PF_BIN}" host approve "${RUN_ID}" >/dev/null
fi
MAT_JSON="$("${PF_BIN}" host materialize "${RUN_ID}" --artifact ARCHITECTURE.md --to docs/ARCHITECTURE.md)"
printf '%s\n' "${MAT_JSON}" | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d.get("ok"), d' \
  || die "materialize failed: ${MAT_JSON}"
[[ -f "${ARCH_PATH}" ]] || die "expected materialized file missing: ${ARCH_PATH}"
log "materialized ${ARCH_PATH} ($(wc -c < "${ARCH_PATH}" | tr -d ' ') bytes)"

log "PASS (opencode ${OPENCODE_VERSION}; tools + mock materialize)"
exit 0
