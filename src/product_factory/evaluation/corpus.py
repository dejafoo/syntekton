"""Fixture corpus identity keyed by pack/skill/connector versions (PMX / SD6)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.evaluation.cases import (
    SD6_CORPUS_CATEGORIES,
    SD6_FOUNDATION_CASE_IDS,
    CorpusCategory,
    EvalCase,
)
from product_factory.evaluation.loader import load_eval_cases


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
    sd6_case_ids: list[str] = Field(default_factory=list)
    sd6_category_counts: dict[str, int] = Field(default_factory=dict)


class Sd6CorpusCatalog(BaseModel):
    """SD6 foundation corpus coverage report."""

    corpus_id: str
    required_case_ids: list[str]
    present_case_ids: list[str]
    missing_case_ids: list[str]
    category_counts: dict[str, int]
    complete: bool
    cases: list[EvalCase] = Field(default_factory=list)


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


def build_sd6_corpus_catalog(
    *,
    project_root: Path,
    cases_dir: Path | None = None,
    corpus_id: str = "sd6-foundation",
) -> Sd6CorpusCatalog:
    """Load and validate the SD6 twelve-case foundation catalog."""
    root = project_root.resolve()
    cases_dir = (cases_dir or root / "tests" / "eval_cases").resolve()
    cases = [
        c
        for c in load_eval_cases(cases_dir)
        if c.id in SD6_FOUNDATION_CASE_IDS or c.metadata.get("sd6_corpus")
    ]
    by_id = {c.id: c for c in cases}
    present = [cid for cid in SD6_FOUNDATION_CASE_IDS if cid in by_id]
    missing = [cid for cid in SD6_FOUNDATION_CASE_IDS if cid not in by_id]
    counts: dict[str, int] = {cat: 0 for cat in SD6_CORPUS_CATEGORIES}
    for case in (by_id[cid] for cid in present):
        category: CorpusCategory | None = case.corpus_category
        if category is not None:
            counts[category] = counts.get(category, 0) + 1
    complete = not missing and all(counts.get(cat, 0) >= 2 for cat in SD6_CORPUS_CATEGORIES)
    return Sd6CorpusCatalog(
        corpus_id=corpus_id,
        required_case_ids=list(SD6_FOUNDATION_CASE_IDS),
        present_case_ids=present,
        missing_case_ids=missing,
        category_counts=counts,
        complete=complete,
        cases=[by_id[cid] for cid in present],
    )


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
    sd6_case_ids: list[str] = []
    sd6_counts: dict[str, int] = defaultdict(int)

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
            if case_id.startswith("sd6_") or case_id in SD6_FOUNDATION_CASE_IDS:
                sd6_case_ids.append(case_id)
                data = _load_yaml(path)
                category = data.get("corpus_category")
                if isinstance(category, str):
                    sd6_counts[category] += 1

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

    promotion_config = root / "config" / "evaluation" / "sd6_promotion.yaml"
    if promotion_config.is_file():
        components.append(
            CorpusComponent(
                kind="promotion_config",
                id="sd6_promotion.yaml",
                path="config/evaluation/sd6_promotion.yaml",
                content_sha256=_sha256_file(promotion_config),
            )
        )

    identity = {
        "corpus_id": corpus_id,
        "components": [c.model_dump() for c in components],
        "case_ids": case_ids,
        "pack_versions": pack_versions,
        "skill_versions": skill_versions,
        "connector_ids": connector_ids,
        "sd6_case_ids": sd6_case_ids,
        "sd6_category_counts": dict(sd6_counts),
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
        sd6_case_ids=sd6_case_ids,
        sd6_category_counts=dict(sd6_counts),
    )
