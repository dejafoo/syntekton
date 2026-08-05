"""Versioned public-domain reference packs (PM5.C / G4).

Domain reference packs supply compact, digest-stable evidence pointers. They
never grant tools, connectors, credentials, or mutation authority. Skills remain
method-only; workflows and the broker remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.domain.errors import ConfigurationError

DOMAIN_PACK_DIRNAME = "domain"
DIGEST_KEY_PREFIX = "domain_pack:"
FORBIDDEN_CONTENT_MARKERS = (
    "api_key",
    "password",
    "secret",
    "bearer ",
    "private_key",
    "ehr://",
    "prod.internal",
    "production.endpoint",
)


class DomainPackGrants(BaseModel):
    """Authority claims a pack may declare — must stay empty."""

    model_config = {"extra": "forbid"}

    additional_tool_classes: list[str] = Field(default_factory=list)
    additional_authority: list[str] = Field(default_factory=list)


class DomainReferencePack(BaseModel):
    """One resolved public-domain reference pack with a content digest."""

    model_config = {"extra": "forbid"}

    id: str
    version: str = "0.0.0"
    title: str = ""
    domain: str = ""
    kind: str = "domain_reference"
    data_classification: str = "synthetic"
    source_policy_profile: str | None = None
    required_review: str | None = None
    permitted_workflows: list[str] = Field(default_factory=list)
    permitted_capabilities: list[str] = Field(default_factory=list)
    grants: DomainPackGrants = Field(default_factory=DomainPackGrants)
    owner: str = ""
    status: str = "active"
    content_ref: str = "references.yaml"
    description: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    path: str = ""

    @property
    def digest(self) -> str:
        payload = {
            "manifest": self.model_dump(
                mode="json",
                exclude={"content", "path"},
            ),
            "content": self.content,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def as_manifest_entry(self) -> dict[str, str]:
        return {f"{DIGEST_KEY_PREFIX}{self.id}": self.digest}

    def asserts_no_authority(self) -> None:
        if self.grants.additional_tool_classes or self.grants.additional_authority:
            raise ConfigurationError(
                f"Domain pack {self.id!r} must not declare additional authority",
                details={
                    "pack_id": self.id,
                    "additional_tool_classes": list(self.grants.additional_tool_classes),
                    "additional_authority": list(self.grants.additional_authority),
                },
            )

    def asserts_no_secrets(self) -> None:
        blob = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
        ).lower()
        hits = [marker for marker in FORBIDDEN_CONTENT_MARKERS if marker in blob]
        if hits:
            raise ConfigurationError(
                f"Domain pack {self.id!r} contains forbidden secret/endpoint markers",
                details={"pack_id": self.id, "markers": hits},
            )


class DomainPackRegistry:
    """Domain packs discovered under ``<packs_root>/domain/*/manifest.yaml``."""

    def __init__(self, packs: list[DomainReferencePack] | None = None) -> None:
        self._packs: dict[str, DomainReferencePack] = {p.id: p for p in packs or []}

    @classmethod
    def load(cls, packs_root: Path) -> DomainPackRegistry:
        root = Path(packs_root) / DOMAIN_PACK_DIRNAME
        if not root.is_dir():
            return cls([])
        packs: list[DomainReferencePack] = []
        for manifest_path in sorted(root.glob("*/manifest.yaml")):
            raw = manifest_path.read_text(encoding="utf-8")
            data: Any = yaml.safe_load(raw) or {}
            if not isinstance(data, dict):
                raise ConfigurationError(
                    f"Domain pack manifest must be a mapping: {manifest_path}",
                    details={"path": str(manifest_path)},
                )
            data.setdefault("id", manifest_path.parent.name)
            content_ref = str(data.get("content_ref") or "references.yaml")
            content_path = manifest_path.parent / content_ref
            content: dict[str, Any] = {}
            if content_path.is_file():
                loaded = yaml.safe_load(content_path.read_text(encoding="utf-8")) or {}
                if not isinstance(loaded, dict):
                    raise ConfigurationError(
                        f"Domain pack content must be a mapping: {content_path}",
                        details={"path": str(content_path)},
                    )
                content = loaded
            pack = DomainReferencePack.model_validate(
                {**data, "content": content, "path": str(manifest_path.parent)}
            )
            if pack.status != "active":
                continue
            pack.asserts_no_authority()
            pack.asserts_no_secrets()
            packs.append(pack)
        return cls(packs)

    def get(self, pack_id: str) -> DomainReferencePack | None:
        return self._packs.get(pack_id)

    def require(self, pack_id: str) -> DomainReferencePack:
        pack = self.get(pack_id)
        if pack is None:
            raise ConfigurationError(
                f"Unknown domain reference pack: {pack_id!r}",
                details={"pack_id": pack_id, "known": self.ids()},
            )
        return pack

    def ids(self) -> list[str]:
        return sorted(self._packs)

    def digests(self) -> dict[str, str]:
        return {f"{DIGEST_KEY_PREFIX}{pack.id}": pack.digest for pack in self._packs.values()}


def resolve_domain_reference_pack(
    pack_id: str,
    *,
    packs_root: Path,
) -> DomainReferencePack:
    return DomainPackRegistry.load(packs_root).require(pack_id)


def resolve_request_domain_packs(
    request: Any,
    *,
    packs_root: Path,
) -> list[DomainReferencePack]:
    """Resolve domain packs named in pack_input (single id or list)."""

    pack_input = getattr(request, "pack_input", None) or {}
    raw = pack_input.get("domain_reference_pack") or pack_input.get("domain_reference_packs")
    if not raw:
        return []
    ids: list[str]
    if isinstance(raw, str):
        ids = [raw.strip()] if raw.strip() else []
    elif isinstance(raw, list):
        ids = [str(item).strip() for item in raw if str(item).strip()]
    else:
        raise ConfigurationError(
            "domain_reference_pack must be a string or list of strings",
            details={"value_type": type(raw).__name__},
        )
    registry = DomainPackRegistry.load(packs_root)
    return [registry.require(pack_id) for pack_id in ids]


__all__ = [
    "DIGEST_KEY_PREFIX",
    "DOMAIN_PACK_DIRNAME",
    "DomainPackGrants",
    "DomainPackRegistry",
    "DomainReferencePack",
    "resolve_domain_reference_pack",
    "resolve_request_domain_packs",
]
