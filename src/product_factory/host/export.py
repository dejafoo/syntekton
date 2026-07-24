"""Evidence bundle export for host `export-bundle` (P3.E)."""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from product_factory.observability.redaction import redact_value
from product_factory.persistence.database import Database


def export_evidence_bundle(
    *,
    pf_root: Path,
    db: Database,
    run_id: str,
    as_zip: bool = True,
) -> dict[str, Any]:
    """Write a redaction-aware evidence bundle (zip or directory).

    Contents: bundle manifest, run manifest, plan, validations, patch/report,
    cost summary, and event cursor range. Secrets in JSON payloads are redacted.
    """
    run_dir = pf_root / "runs" / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory missing for {run_id}")

    row = db.get_run(run_id)
    if not row:
        raise FileNotFoundError(f"Unknown run {run_id}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    export_root = run_dir / "export"
    export_root.mkdir(parents=True, exist_ok=True)
    staging = export_root / f"bundle-{stamp}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    included: list[str] = []
    output_dir = run_dir / "output"

    def _copy_if_exists(src: Path, dest_name: str) -> None:
        if src.is_file():
            shutil.copy2(src, staging / dest_name)
            included.append(dest_name)

    def _write_json(name: str, payload: Any) -> None:
        (staging / name).write_text(
            json.dumps(redact_value(payload), indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        included.append(name)

    _copy_if_exists(run_dir / "run-manifest.json", "run-manifest.json")
    _copy_if_exists(output_dir / "plan.json", "plan.json")
    _copy_if_exists(output_dir / "proposed.patch", "proposed.patch")
    _copy_if_exists(output_dir / "ARCHITECTURE.md", "ARCHITECTURE.md")
    _copy_if_exists(output_dir / "EVIDENCE_REPORT.md", "EVIDENCE_REPORT.md")
    _copy_if_exists(output_dir / "approval.json", "approval.json")
    _copy_if_exists(output_dir / "run-summary.md", "run-summary.md")
    _copy_if_exists(output_dir / "compiler-report.json", "compiler-report.json")
    _copy_if_exists(output_dir / "revisions.jsonl", "revisions.jsonl")

    validations = [
        {
            "id": r["id"],
            "run_id": r["run_id"],
            "task_id": r["task_id"],
            "result": json.loads(r["result_json"]) if r.get("result_json") else {},
        }
        for r in db.list_validator_results(run_id)
    ]
    _write_json("validations.json", validations)

    usage = json.loads(row.get("usage_json") or "{}")
    cost_summary = {
        "run_id": run_id,
        "status": row["status"],
        "usage": usage,
        "budget": json.loads(row["budget_json"]) if row.get("budget_json") else None,
        "estimated_cost_usd": usage.get("estimated_cost_usd"),
    }
    _write_json("cost-summary.json", cost_summary)

    events = db.list_events(run_id=run_id, after_seq=0, limit=10_000)
    seqs = [int(e["seq"]) for e in events]
    event_range = {
        "after_seq": 0,
        "first_seq": min(seqs) if seqs else 0,
        "last_seq": max(seqs) if seqs else 0,
        "count": len(events),
    }
    redacted_events = []
    for e in events:
        payload = json.loads(e["payload_json"]) if e.get("payload_json") else {}
        redacted_events.append(
            {
                "seq": e["seq"],
                "event_id": e["event_id"],
                "occurred_at": e["occurred_at"],
                "type": e["event_type"],
                "run_id": e["run_id"],
                "task_id": e.get("task_id"),
                "severity": e["severity"],
                "summary": e["summary"],
                "payload": redact_value(payload),
            }
        )
    _write_json("events.json", {"range": event_range, "items": redacted_events})

    bundle_manifest = {
        "protocol": "product-factory.evidence-bundle/v1",
        "run_id": run_id,
        "exported_at": datetime.now(UTC).isoformat(),
        "status": row["status"],
        "workflow_type": row["workflow_type"],
        "event_range": event_range,
        "files": [*included, "bundle-manifest.json"],
        "redaction": "payloads_and_json_via_redact_value",
    }
    (staging / "bundle-manifest.json").write_text(
        json.dumps(redact_value(bundle_manifest), indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    files = list(bundle_manifest["files"])

    if as_zip:
        zip_path = export_root / f"evidence-bundle-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in staging.rglob("*"):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(staging).as_posix())
        shutil.rmtree(staging)
        return {
            "format": "zip",
            "path": str(zip_path),
            "relative_path": str(zip_path.relative_to(pf_root)),
            "files": files,
            "event_range": event_range,
        }

    return {
        "format": "dir",
        "path": str(staging),
        "relative_path": str(staging.relative_to(pf_root)),
        "files": files,
        "event_range": event_range,
    }
