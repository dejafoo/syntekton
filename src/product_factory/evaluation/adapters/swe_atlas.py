"""SWE Atlas external evaluation adapter (SD6 minimal).

Maps version-pinned SWE Atlas case records into ``EvalCase`` without claiming a
live suite run. Durable adapter/version/case mapping records are written under
``.product-factory/external-adapters/`` (or a caller-supplied root).

Terminal-Bench/Harbor is next; DeepSWE remains a later licensing/compatibility
gate and is not implemented here.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from product_factory.evaluation.cases import EvalCase


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class SweAtlasCaseMapping(BaseModel):
    """Maps one SWE Atlas native case id onto a Product Factory EvalCase id."""

    atlas_case_id: str
    eval_case_id: str
    task_class: Literal[
        "repository_investigation",
        "test_generation",
        "refactoring",
        "code_understanding",
        "other",
    ] = "other"
    atlas_version: str
    native_metrics: list[str] = Field(default_factory=list)
    notes: str = ""


class SweAtlasAdapterRecord(BaseModel):
    """Durable adapter/version provenance for an external subset."""

    adapter_id: str = "swe_atlas"
    adapter_version: str
    atlas_release: str
    schema_version: str = "sd6.swe_atlas.v1"
    case_mappings: list[SweAtlasCaseMapping] = Field(default_factory=list)
    content_sha256: str = ""
    license_note: str = "Caller must record the SWE Atlas license and subset scope."
    live_run: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)

    def with_content_hash(self) -> SweAtlasAdapterRecord:
        payload = self.model_dump(exclude={"content_sha256"})
        digest = _sha256_bytes(_stable_json(payload).encode("utf-8"))
        return self.model_copy(update={"content_sha256": digest})


class SweAtlasCaseLoader:
    """Minimal CaseLoader for version-pinned SWE Atlas records."""

    suite_name = "swe_atlas"

    def __init__(
        self,
        records: list[dict[str, Any]] | None = None,
        *,
        atlas_version: str = "unspecified",
        adapter_version: str = "0.1.0",
    ) -> None:
        self.records = records or []
        self.atlas_version = atlas_version
        self.adapter_version = adapter_version

    def load(self, *, limit: int | None = None) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for raw in self.records:
            atlas_id = str(raw.get("id") or raw.get("atlas_case_id") or "swe-atlas-unknown")
            eval_id = str(raw.get("eval_case_id") or f"swe_atlas_{atlas_id}")
            workflow = raw.get("workflow_type") or "code_change"
            cases.append(
                EvalCase(
                    id=eval_id,
                    workflow_type=workflow,  # type: ignore[arg-type]
                    request=str(raw.get("prompt") or raw.get("request") or raw.get("task") or ""),
                    repository=raw.get("repository"),
                    tags=list(raw.get("tags") or ["external", "swe_atlas"]),
                    suite="swe_atlas",
                    corpus_category=raw.get("corpus_category") or "repository_change",
                    acceptance_criteria=list(raw.get("acceptance_criteria") or []),
                    reference_hints=raw.get("reference_hints"),
                    isolation_targets=list(raw.get("isolation_targets") or ["implementation"]),
                    expected_files=list(raw.get("expected_files") or []),
                    smoke_commands=list(
                        raw.get("smoke_commands")
                        or (
                            ["python_tests"]
                            if (raw.get("workflow_type") or "code_change") == "code_change"
                            else []
                        )
                    ),
                    metadata={
                        "external_suite": "swe_atlas",
                        "atlas_case_id": atlas_id,
                        "atlas_version": self.atlas_version,
                        "adapter_version": self.adapter_version,
                        "task_class": raw.get("task_class") or "other",
                        "sanitization": raw.get("sanitization") or "external_suite_mapping",
                        "sd6_corpus": False,
                        **dict(raw.get("metadata") or {}),
                    },
                )
            )
        if limit is not None:
            return cases[:limit]
        return cases

    def build_mapping_record(self) -> SweAtlasAdapterRecord:
        mappings: list[SweAtlasCaseMapping] = []
        for raw in self.records:
            atlas_id = str(raw.get("id") or raw.get("atlas_case_id") or "swe-atlas-unknown")
            eval_id = str(raw.get("eval_case_id") or f"swe_atlas_{atlas_id}")
            task_class = raw.get("task_class") or "other"
            mappings.append(
                SweAtlasCaseMapping(
                    atlas_case_id=atlas_id,
                    eval_case_id=eval_id,
                    task_class=task_class,  # type: ignore[arg-type]
                    atlas_version=self.atlas_version,
                    native_metrics=list(raw.get("native_metrics") or []),
                    notes=str(raw.get("notes") or ""),
                )
            )
        record = SweAtlasAdapterRecord(
            adapter_version=self.adapter_version,
            atlas_release=self.atlas_version,
            case_mappings=mappings,
            live_run=False,
            metadata={"status": "minimal", "terminal_bench": "next", "deepswe": "deferred"},
        )
        return record.with_content_hash()


class ExternalAdapterStore:
    """Persist durable external adapter mapping records."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.adapters_dir = self.root / "external-adapters"
        self.adapters_dir.mkdir(parents=True, exist_ok=True)

    def save_swe_atlas(self, record: SweAtlasAdapterRecord) -> Path:
        hashed = record if record.content_sha256 else record.with_content_hash()
        path = (
            self.adapters_dir
            / f"swe_atlas-{hashed.adapter_version}-{hashed.content_sha256[:12]}.json"
        )
        path.write_text(hashed.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def list_swe_atlas(self) -> list[SweAtlasAdapterRecord]:
        records: list[SweAtlasAdapterRecord] = []
        for path in sorted(self.adapters_dir.glob("swe_atlas-*.json")):
            records.append(
                SweAtlasAdapterRecord.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return records
