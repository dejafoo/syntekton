#!/usr/bin/env bash
# Gated OpenCode remote smoke against the Docker mock sandbox (PM3).
#
# Default: skip cleanly (exit 0) when `opencode` or Docker/compose is unavailable.
# OPENCODE_INTEGRATION=1: fail if opencode or Docker/compose is missing.
#
# Asserts plugin tool registration via `opencode serve`, then drives the remote
# mock path with Python RemotePfClient (submit → wait → review → approve/land
# when delivery is available). OpenCode tool *execution* is not driven through
# the serve API; merge/land mirrors the plugin HTTP path instead.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLUGIN_DIR="${REPO_ROOT}/integrations/opencode-plugin"
COMPOSE_FILE="${REPO_ROOT}/examples/remote/docker-compose.yml"
UP_SCRIPT="${REPO_ROOT}/scripts/docker_remote_up.sh"
REQUIRED_TOOLS=(pf_run pf_wait pf_review pf_merge pf_decline)
FORCE_INTEGRATION="${OPENCODE_INTEGRATION:-0}"
COMPOSE_STARTED=0

log() { printf 'opencode-remote-smoke: %s\n' "$*"; }
die() { log "FAIL: $*"; exit 1; }

force_required() {
  [[ "${FORCE_INTEGRATION}" == "1" || "${FORCE_INTEGRATION}" == "true" ]]
}

# --- gates ------------------------------------------------------------------
if ! command -v opencode >/dev/null 2>&1; then
  if force_required; then
    die "opencode not on PATH (OPENCODE_INTEGRATION=1 requires it)"
  fi
  log "SKIP: opencode not on PATH (set OPENCODE_INTEGRATION=1 to require it)"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  die "curl is required for the OpenCode serve health/tool probe"
fi
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required to parse OpenCode JSON / drive RemotePfClient"
fi

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  if force_required; then
    die "docker daemon unavailable (OPENCODE_INTEGRATION=1 requires Docker remote)"
  fi
  log "SKIP: docker daemon unavailable (set OPENCODE_INTEGRATION=1 to require it)"
  exit 0
fi
if ! docker compose version >/dev/null 2>&1; then
  if force_required; then
    die "docker compose unavailable (OPENCODE_INTEGRATION=1 requires it)"
  fi
  log "SKIP: docker compose unavailable (set OPENCODE_INTEGRATION=1 to require it)"
  exit 0
fi
if [[ ! -f "${UP_SCRIPT}" ]]; then
  die "missing ${UP_SCRIPT}"
fi
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  die "missing ${COMPOSE_FILE}"
fi

OPENCODE_VERSION="$(opencode --version 2>/dev/null | head -n 1 | tr -d '\r')"
log "opencode --version: ${OPENCODE_VERSION}"

export PRODUCT_FACTORY_REMOTE_URL="${PRODUCT_FACTORY_REMOTE_URL:-http://127.0.0.1:8765}"
export PRODUCT_FACTORY_OBSERVE_TOKEN="${PRODUCT_FACTORY_OBSERVE_TOKEN:-test-token}"
export PRODUCT_FACTORY_OBSERVE_URL="${PRODUCT_FACTORY_OBSERVE_URL:-${PRODUCT_FACTORY_REMOTE_URL}}"
export PRODUCT_FACTORY_FORCE_MOCK=1

# Preserve operator HOME for Docker CLI plugins before OpenCode isolation.
ORIGINAL_HOME="${HOME}"
docker_bin() { HOME="${ORIGINAL_HOME}" docker "$@"; }

# --- docker remote (before HOME isolation so compose plugins resolve) -------
META_URL="${PRODUCT_FACTORY_REMOTE_URL%/}/api/v1/meta"
if curl -sf \
  -H "Authorization: Bearer ${PRODUCT_FACTORY_OBSERVE_TOKEN}" \
  -H "Accept: application/json" \
  "${META_URL}" >/dev/null 2>&1; then
  log "reusing healthy remote at ${PRODUCT_FACTORY_REMOTE_URL}"
  COMPOSE_STARTED=1
else
  log "bringing up Docker remote via ${UP_SCRIPT}"
  bash "${UP_SCRIPT}"
  COMPOSE_STARTED=1
fi

# --- temp project + cleanup -------------------------------------------------
TMP="$(mktemp -d "${TMPDIR:-/tmp}/opencode-remote-smoke.XXXXXX")"
cleanup() {
  if [[ -n "${SERVE_PID:-}" ]] && kill -0 "${SERVE_PID}" 2>/dev/null; then
    kill "${SERVE_PID}" 2>/dev/null || true
    wait "${SERVE_PID}" 2>/dev/null || true
  fi
  if [[ "${OPENCODE_SMOKE_KEEP:-0}" == "1" ]]; then
    log "keeping temp dir: ${TMP}"
    log "keeping compose (OPENCODE_SMOKE_KEEP=1)"
  else
    rm -rf "${TMP}"
    if [[ "${COMPOSE_STARTED}" == "1" ]]; then
      log "tearing down compose"
      docker_bin compose -f "${COMPOSE_FILE}" down --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
}
trap cleanup EXIT

HOME_ISO="${TMP}/home"
PROJ="${TMP}/proj"
LAND_WS="${TMP}/land_ws"
mkdir -p \
  "${HOME_ISO}/.config/opencode" \
  "${HOME_ISO}/.local/share/opencode/log" \
  "${HOME_ISO}/.cache/opencode" \
  "${HOME_ISO}/.local/state" \
  "${PROJ}/.opencode/plugins"

# Isolate from the developer's ~/.config/opencode (MCP, other plugins).
export HOME="${HOME_ISO}"
export XDG_CONFIG_HOME="${HOME_ISO}/.config"
export XDG_DATA_HOME="${HOME_ISO}/.local/share"
export XDG_CACHE_HOME="${HOME_ISO}/.cache"
export XDG_STATE_HOME="${HOME_ISO}/.local/state"
export OPENCODE_DISABLE_AUTOUPDATE=1
export OPENCODE_DISABLE_MODELS_FETCH=1

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
printf 'OpenCode remote plugin smoke fixture\n' > "${PROJ}/README.md"
cat > "${PROJ}/.gitignore" <<'EOF'
.opencode/
.product-factory/
EOF
git -C "${PROJ}" init -q
git -C "${PROJ}" config user.email "smoke@product-factory.local"
git -C "${PROJ}" config user.name "OpenCode Remote Smoke"
git -C "${PROJ}" add README.md opencode.json .gitignore
git -C "${PROJ}" commit -q -m "smoke fixture"

log "temp project: ${PROJ}"

# --- serve + tool visibility ------------------------------------------------
PORT="${OPENCODE_SMOKE_PORT:-$((18000 + RANDOM % 2000))}"
SERVE_LOG="${TMP}/serve.log"
(
  cd "${PROJ}"
  # Plugin must see remote mode while registering tools.
  export PRODUCT_FACTORY_REMOTE_URL
  export PRODUCT_FACTORY_OBSERVE_TOKEN
  export PRODUCT_FACTORY_FORCE_MOCK
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
log "health: $(curl -sf "${HEALTH_URL}")"

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

# --- remote mock drive (RemotePfClient; plugin-equivalent HTTP) -------------
# Copy the server fixture so LandingAdapter base_revision checks can pass.
log "copying sample_api fixture for landing workspace"
rm -rf "${LAND_WS}"
docker_bin compose -f "${COMPOSE_FILE}" cp \
  "product-factory:/data/repos/sample_api" "${LAND_WS}" >/dev/null 2>&1
[[ -d "${LAND_WS}/.git" ]] || die "land workspace missing .git after docker compose cp"

cd "${REPO_ROOT}"
LAND_WS="${LAND_WS}" COMPOSE_FILE="${COMPOSE_FILE}" ORIGINAL_HOME="${ORIGINAL_HOME}" \
  uv run python - <<'PY'
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from product_factory.delivery import LandingAdapter, LandingReceipt
from product_factory.remote.client import RemotePfClient

land_ws = Path(os.environ["LAND_WS"]).resolve()
compose = os.environ["COMPOSE_FILE"]
url = os.environ["PRODUCT_FACTORY_REMOTE_URL"].rstrip("/")
token = os.environ["PRODUCT_FACTORY_OBSERVE_TOKEN"]
docker_env = {**os.environ, "HOME": os.environ["ORIGINAL_HOME"]}


def log(msg: str) -> None:
    print(f"opencode-remote-smoke: {msg}", flush=True)


def die(msg: str) -> None:
    log(f"FAIL: {msg}")
    sys.exit(1)


resolved = subprocess.run(
    [
        "docker",
        "compose",
        "-f",
        compose,
        "exec",
        "-T",
        "product-factory",
        "git",
        "-C",
        "/data/repos/sample_api",
        "rev-parse",
        "HEAD",
    ],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
    env=docker_env,
).stdout.strip()
if not resolved:
    die("could not resolve sample_api HEAD inside container")

local_head = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=land_ws,
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
if local_head != resolved:
    die(f"land workspace HEAD {local_head} != server sample_api {resolved}")

with RemotePfClient(base_url=url, token=token, timeout=180.0) as client:
    meta = client.meta()
    log(
        "meta: remote_mode={rm} delivery_support={ds} repos={repos}".format(
            rm=meta.get("remote_mode"),
            ds=meta.get("delivery_support"),
            repos=",".join(meta.get("repository_ids") or []),
        )
    )
    if not meta.get("remote_mode"):
        die("expected remote_mode=true from Docker sandbox")

    # Prefer git_ref workspace when advertised; fall back to registered repo id.
    kinds = set(meta.get("supported_workspace_kinds") or [])
    submit_kwargs: dict = {
        "request_text": (
            "Investigate health-check coverage and propose a short technical plan "
            "for the sample_api fixture."
        ),
        "workflow_type": "technical_plan",
        "mock": True,
        "sync": True,
    }
    if "git_ref" in kinds:
        submit_kwargs["workspace"] = {
            "kind": "git_ref",
            "repository_id": "sample_api",
            "ref": "refs/heads/main",
            "commit": resolved,
        }
        log(f"submitting technical_plan with git_ref commit={resolved[:12]}")
    else:
        submit_kwargs["repository_id"] = "sample_api"
        log("submitting technical_plan with repository_id=sample_api")

    submitted = client.submit(**submit_kwargs)
    if not submitted.ok or not submitted.run_id:
        die(f"submit failed: {submitted.model_dump()}")
    run_id = submitted.run_id
    log(f"submitted run_id={run_id} status={submitted.status}")

    waited = client.wait(run_id, timeout=180.0, wanted={"completed", "awaiting_approval", "failed"})
    if not waited.ok:
        die(f"wait failed: {waited.model_dump()}")
    status = waited.status or ""
    log(f"waited status={status}")
    if status not in {"completed", "awaiting_approval"}:
        die(f"unexpected status after wait: {status}")

    inspected = client.inspect(run_id)
    if not inspected.ok or inspected.data is None:
        die(f"inspect/review failed: {inspected.model_dump()}")
    log("review/inspect ok")

    if not meta.get("delivery_support"):
        log("delivery_support=false; skipping merge/land (tools + submit/wait/review only)")
        print("PASS (tools + remote submit/wait/review; delivery not advertised)")
        sys.exit(0)

    # pf_merge path: confirm → approve apply:false → download/verify → land → receipt
    if status == "awaiting_approval":
        approved = client.approve(run_id, apply=False)
        if not approved.ok:
            die(f"approve(apply=false) failed: {approved.model_dump()}")
        log(f"approved apply=false → status={approved.status}")

    manifest = client.delivery(run_id)
    if not manifest.entries:
        die("delivery manifest has no entries")
    log(
        f"delivery_id={manifest.delivery_id} entries={len(manifest.entries)} "
        f"base={manifest.base_revision[:12]}"
    )

    result = LandingAdapter().land(
        manifest,
        workspace_root=land_ws,
        blob_loader=lambda digest: client.delivery_blob(run_id, digest),
    )
    for rel in result.landed_paths:
        path = land_ws / rel
        if not path.is_file():
            die(f"expected landed file missing: {path}")
        log(f"landed {rel} ({path.stat().st_size} bytes)")

    receipt = client.record_landing(
        run_id,
        LandingReceipt(
            manifest_sha256=result.manifest_sha256,
            base_revision=result.base_revision,
            status="landed",
            landed_paths=list(result.landed_paths),
            client="opencode-remote-smoke",
        ),
    )
    if receipt.get("status") != "landed":
        die(f"unexpected receipt: {json.dumps(receipt)}")
    log(f"receipt recorded id={receipt.get('receipt_id')}")

print("PASS (tools + remote submit/wait/review + delivery land/receipt)")
PY

log "PASS (opencode ${OPENCODE_VERSION}; remote tools + mock lifecycle)"
exit 0
