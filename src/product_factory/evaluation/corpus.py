"""Fixture corpus identity keyed by pack/skill/connector versions (PMX)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


class CorpusComponent(BaseModel):
    kind: str
    id: str
    version: str | None = None
    path: str
    content_sha256: str


class CorpusSnapshot(BaseModel):
    """Immutable identity for an evaluation/fixture slice."""

    corpus_id: str
    content_sha256: str
    components: list[CorpusComponent] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    pack_versions: dict[str, str] = Field(default_factory=dict)
    skill_versions: dict[str, str] = Field(default_factory=dict)
    connector_ids: list[str] = Field(default_factory=list)


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _collect_manifest_versions(
    root: Path, *, kind: str
) -> tuple[list[CorpusComponent], dict[str, str]]:
    components: list[CorpusComponent] = []
    versions: dict[str, str] = {}
    if not root.is_dir():
        return components, versions
    for manifest in sorted(root.rglob("manifest.yaml")):
        data = _load_yaml(manifest)
        item_id = str(data.get("id") or manifest.parent.name)
        version = str(data.get("version") or "")
        digest = _sha256_file(manifest)
        components.append(
            CorpusComponent(
                kind=kind,
                id=item_id,
                version=version or None,
                path=str(manifest),
                content_sha256=digest,
            )
        )
        if version:
            versions[item_id] = version
        # Include sibling content referenced by the manifest when present.
        content_ref = data.get("content_ref")
        if content_ref:
            sibling = manifest.parent / str(content_ref)
            if sibling.is_file():
                components.append(
                    CorpusComponent(
                        kind=f"{kind}_content",
                        id=f"{item_id}:{content_ref}",
                        version=version or None,
                        path=str(sibling),
                        content_sha256=_sha256_file(sibling),
                    )
                )
    return components, versions


def build_corpus_snapshot(
    *,
    project_root: Path,
    cases_dir: Path | None = None,
    fixtures_dir: Path | None = None,
    corpus_id: str = "pmx-default",
) -> CorpusSnapshot:
    """Hash eval cases, PM5 fixtures, packs, skills, and connector manifests."""
    root = project_root.resolve()
    cases_dir = (cases_dir or root / "tests" / "eval_cases").resolve()
    fixtures_dir = (fixtures_dir or root / "tests" / "fixtures").resolve()

    components: list[CorpusComponent] = []
    case_ids: list[str] = []

    if cases_dir.is_dir():
        for path in sorted(cases_dir.glob("*.yaml")):
            digest = _sha256_file(path)
            case_id = path.stem
            case_ids.append(case_id)
            components.append(
                CorpusComponent(
                    kind="eval_case",
                    id=case_id,
                    path=str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                    content_sha256=digest,
                )
            )

    for slice_name in ("release", "ops", "deploy", "ci", "observability", "domain"):
        slice_dir = fixtures_dir / slice_name
        if not slice_dir.is_dir():
            continue
        for path in sorted(p for p in slice_dir.rglob("*") if p.is_file()):
            rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            components.append(
                CorpusComponent(
                    kind="fixture",
                    id=f"{slice_name}:{path.name}",
                    path=rel,
                    content_sha256=_sha256_file(path),
                )
            )

    pack_components, pack_versions = _collect_manifest_versions(root / "packs", kind="pack")
    skill_components, skill_versions = _collect_manifest_versions(root / "skills", kind="skill")
    components.extend(pack_components)
    components.extend(skill_components)

    connector_ids: list[str] = []
    connectors_yaml = root / "config" / "connectors.yaml"
    if connectors_yaml.is_file():
        data = _load_yaml(connectors_yaml)
        connectors = data.get("connectors") or {}
        if isinstance(connectors, dict):
            connector_ids = sorted(str(k) for k in connectors)
        components.append(
            CorpusComponent(
                kind="connectors_config",
                id="connectors.yaml",
                path="config/connectors.yaml",
                content_sha256=_sha256_file(connectors_yaml),
            )
        )

    identity = {
        "corpus_id": corpus_id,
        "components": [c.model_dump() for c in components],
        "case_ids": case_ids,
        "pack_versions": pack_versions,
        "skill_versions": skill_versions,
        "connector_ids": connector_ids,
    }
    content_sha256 = _sha256_bytes(_stable_json(identity).encode("utf-8"))
    return CorpusSnapshot(
        corpus_id=corpus_id,
        content_sha256=content_sha256,
        components=components,
        case_ids=case_ids,
        pack_versions=pack_versions,
        skill_versions=skill_versions,
        connector_ids=connector_ids,
    )
