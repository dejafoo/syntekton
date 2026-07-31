#!/usr/bin/env bash
# Build + start the PM3.0 Docker remote mock sandbox and wait for GET /api/v1/meta.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/examples/remote/docker-compose.yml"

export PRODUCT_FACTORY_OBSERVE_TOKEN="${PRODUCT_FACTORY_OBSERVE_TOKEN:-test-token}"
export PRODUCT_FACTORY_OBSERVE_URL="${PRODUCT_FACTORY_OBSERVE_URL:-http://127.0.0.1:8765}"
export PRODUCT_FACTORY_REMOTE_URL="${PRODUCT_FACTORY_REMOTE_URL:-http://127.0.0.1:8765}"

log() { printf 'docker-remote-up: %s\n' "$*"; }
die() { log "FAIL: $*"; exit 1; }

if ! command -v docker >/dev/null 2>&1; then
  die "docker not on PATH"
fi
if ! docker info >/dev/null 2>&1; then
  die "docker daemon is not available (is Docker running?)"
fi
if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin is required"
fi
if ! command -v curl >/dev/null 2>&1; then
  die "curl is required for the health wait"
fi

log "building image (compose file: ${COMPOSE_FILE})"
docker compose -f "${COMPOSE_FILE}" build

log "starting stack"
docker compose -f "${COMPOSE_FILE}" up -d

META_URL="${PRODUCT_FACTORY_REMOTE_URL%/}/api/v1/meta"
log "waiting for ${META_URL}"
for _ in $(seq 1 90); do
  if curl -sf \
    -H "Authorization: Bearer ${PRODUCT_FACTORY_OBSERVE_TOKEN}" \
    -H "Accept: application/json" \
    "${META_URL}" >/tmp/pf-docker-meta.json 2>/dev/null; then
    log "ready at ${PRODUCT_FACTORY_REMOTE_URL}"
    if command -v python3 >/dev/null 2>&1; then
      python3 - <<'PY'
import json
from pathlib import Path
meta = json.loads(Path("/tmp/pf-docker-meta.json").read_text())
print(
    "meta:",
    "protocol=" + str(meta.get("protocol")),
    "remote_mode=" + str(meta.get("remote_mode")),
    "repos=" + ",".join(meta.get("repository_ids") or []),
)
PY
    else
      cat /tmp/pf-docker-meta.json
      echo
    fi
    exit 0
  fi
  sleep 1
done

log "timed out waiting for meta; recent compose logs:"
docker compose -f "${COMPOSE_FILE}" logs --tail 100 || true
die "product-factory container did not become healthy"
