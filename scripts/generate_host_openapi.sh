#!/usr/bin/env bash
# Regenerate committed host/v2 OpenAPI + JSON Schema snapshots (SD4.C).
set -euo pipefail
cd "$(dirname "$0")/.."

uv run python - <<'PY'
import json
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

with TemporaryDirectory() as tmp:
    app = create_app(Path(tmp) / "pf")
    full = app.openapi()

v2_paths = {k: v for k, v in full.get("paths", {}).items() if k.startswith("/api/v2")}
if "/api/v1/meta" in full.get("paths", {}):
    v2_paths["/api/v1/meta"] = full["paths"]["/api/v1/meta"]

snapshot = {
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
Path("contracts/host/openapi-v2.json").write_text(
    json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)

schemas = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://product-factory.dev/schemas/host/v2.json",
    "title": "product-factory.host/v2",
    "definitions": {
        "HandoffClaim": HandoffClaim.model_json_schema(),
        "SubmitRunV2Body": SubmitRunV2Body.model_json_schema(),
        "HostResponseV2": HostResponseV2.model_json_schema(),
    },
}
Path("schemas/host/v2.json").write_text(
    json.dumps(schemas, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
print("regenerated contracts/host/openapi-v2.json and schemas/host/v2.json")
PY
