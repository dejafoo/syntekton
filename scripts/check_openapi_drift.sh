#!/usr/bin/env bash
# SD4.C — detect drift between live FastAPI OpenAPI export and the committed
# host/v2 snapshot. Coordinate with SD5 verify.sh (call this script from CI).
set -euo pipefail
cd "$(dirname "$0")/.."

SNAPSHOT="${1:-contracts/host/openapi-v2.json}"
SCHEMA_SNAPSHOT="${2:-schemas/host/v2.json}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

uv run python - <<PY
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from product_factory.api.app import create_app
from product_factory.host.protocol_v2 import (
    HOST_PROTOCOL_V2,
    HandoffClaim,
    HostResponseV2,
    SubmitRunV2Body,
    protocol_metadata,
)

snapshot_path = Path("${SNAPSHOT}")
schema_path = Path("${SCHEMA_SNAPSHOT}")

with TemporaryDirectory() as tmp:
    app = create_app(Path(tmp) / "pf")
    full = app.openapi()

v2_paths = {k: v for k, v in full.get("paths", {}).items() if k.startswith("/api/v2")}
if "/api/v1/meta" in full.get("paths", {}):
    v2_paths["/api/v1/meta"] = full["paths"]["/api/v1/meta"]

live = {
    "openapi": full.get("openapi", "3.1.0"),
    "info": {
        "title": "Product Factory Host Protocol",
        "version": "2.0.0",
        "description": "Canonical host/v2 + compatibility meta (SD4.C).",
    },
    "paths": dict(sorted(v2_paths.items())),
    "components": full.get("components", {}),
    "x-product-factory": {
        "host_protocol": HOST_PROTOCOL_V2,
        "protocol_metadata": protocol_metadata(),
    },
}
live_text = json.dumps(live, indent=2, sort_keys=True) + "\n"
committed = snapshot_path.read_text(encoding="utf-8")
if live_text != committed:
    Path("${TMP}/live-openapi.json").write_text(live_text, encoding="utf-8")
    print("OpenAPI host/v2 snapshot drift detected.", file=sys.stderr)
    print(f"  committed: {snapshot_path}", file=sys.stderr)
    print(f"  live dump: ${TMP}/live-openapi.json", file=sys.stderr)
    print("Regenerate with: bash scripts/check_openapi_drift.sh --write", file=sys.stderr)
    sys.exit(1)

live_schema = {
    "\$schema": "https://json-schema.org/draft/2020-12/schema",
    "\$id": "https://product-factory.dev/schemas/host/v2.json",
    "title": "product-factory.host/v2",
    "definitions": {
        "HandoffClaim": HandoffClaim.model_json_schema(),
        "SubmitRunV2Body": SubmitRunV2Body.model_json_schema(),
        "HostResponseV2": HostResponseV2.model_json_schema(),
    },
}
schema_text = json.dumps(live_schema, indent=2, sort_keys=True) + "\n"
if schema_text != schema_path.read_text(encoding="utf-8"):
    print("JSON Schema host/v2 snapshot drift detected.", file=sys.stderr)
    print(f"  committed: {schema_path}", file=sys.stderr)
    sys.exit(1)

print("host OpenAPI/JSON Schema snapshots OK")
PY
