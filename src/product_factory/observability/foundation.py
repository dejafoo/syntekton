"""PM0 foundation projections for host inspect / observability."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def collect_foundation_projections(run_dir: Path) -> dict[str, Any]:
    """Gather schema/receipt/classification/skill digest summaries for a run."""
    artifacts_root = run_dir / "artifacts" / "blobs"
    receipt_summaries: list[dict[str, Any]] = []
    classification_decisions: list[dict[str, Any]] = []
    schema_ids: list[str] = []
    skill_digests: dict[str, str] = {}

    prompts_dir = run_dir / "prompts"
    if prompts_dir.is_dir():
        for path in sorted(prompts_dir.glob("**/task-context*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for skill_id, digest in (data.get("skill_digests") or {}).items():
                skill_digests[str(skill_id)] = str(digest)
            if data.get("primary_skill_id") and data.get("primary_skill_digest"):
                skill_digests[str(data["primary_skill_id"])] = str(data["primary_skill_digest"])

    if artifacts_root.is_dir():
        for path in sorted(artifacts_root.iterdir()):
            if not path.is_file() or path.stat().st_size > 512_000:
                continue
            try:
                text = path.read_text(encoding="utf-8")
                data = json.loads(text)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict):
                continue
            schema_id = data.get("schema_id")
            if isinstance(schema_id, str):
                schema_ids.append(schema_id)
            if schema_id == "connector_receipt.v1":
                receipt_summaries.append(
                    {
                        "sha256": path.name,
                        "connector_id": data.get("connector_id"),
                        "tool_name": data.get("tool_name"),
                        "result_sha256": data.get("result_sha256"),
                        "retrieved_at": data.get("retrieved_at"),
                    }
                )
            if "classification" in data and isinstance(data["classification"], dict):
                classification_decisions.append(data["classification"])
            if data.get("outcome") in {"allow", "redact", "block"} and data.get("rule_version"):
                classification_decisions.append(data)

    # Deduplicate classification by JSON dump
    seen: set[str] = set()
    unique_decisions: list[dict[str, Any]] = []
    for item in classification_decisions:
        key = json.dumps(item, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique_decisions.append(item)

    return {
        "schema_ids": sorted(set(schema_ids)),
        "receipt_summaries": receipt_summaries[:50],
        "classification_decisions": unique_decisions[:50],
        "skill_digests": skill_digests,
    }
