#!/usr/bin/env bash
# Isolated wheel build + install + packaged dashboard/health smoke (SD5).
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$(pwd)"
DIST_DIR="${ROOT}/dist"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pf-package-smoke.XXXXXX")"
cleanup() {
  rm -rf "${SMOKE_DIR}"
}
trap cleanup EXIT

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

STATIC_INDEX="${ROOT}/src/product_factory/api/static/dashboard/index.html"
if [[ ! -f "${STATIC_INDEX}" ]]; then
  echo "packaged dashboard missing at ${STATIC_INDEX}; build dashboard first" >&2
  exit 1
fi

echo "==> uv build (from frozen workspace)"
uv build --out-dir "${DIST_DIR}"

WHEEL="$(ls -1 "${DIST_DIR}"/product_factory-*.whl | head -n 1)"
if [[ -z "${WHEEL}" ]]; then
  echo "no wheel produced under ${DIST_DIR}" >&2
  exit 1
fi
echo "wheel: ${WHEEL}"

echo "==> isolated venv + install wheel[observability]"
uv venv "${SMOKE_DIR}/.venv" --python 3.13
# Install the built distribution with the observability extra. httpx2 clears the
# Starlette TestClient deprecation under this smoke path.
uv pip install --python "${SMOKE_DIR}/.venv/bin/python" "${WHEEL}[observability]" "httpx2>=2.0.0"

echo "==> CLI from installed wheel"
"${SMOKE_DIR}/.venv/bin/product-factory" --help >/dev/null

echo "==> packaged dashboard + /api/v1/health via TestClient"
PRODUCT_FACTORY_FORCE_MOCK=1 "${SMOKE_DIR}/.venv/bin/python" - <<'PY'
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import product_factory.api.app as api_app
from product_factory.api.app import create_app

static = Path(api_app.__file__).with_name("static") / "dashboard" / "index.html"
assert static.is_file(), f"packaged dashboard missing: {static}"

with tempfile.TemporaryDirectory() as tmp:
    app = create_app(Path(tmp))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200, health.text
        assert health.json().get("wal_mode") is True
        dash = client.get("/dashboard/")
        assert dash.status_code == 200, dash.text
        assert "Product Factory" in dash.text
print("package smoke OK")
PY

echo "package smoke OK"
