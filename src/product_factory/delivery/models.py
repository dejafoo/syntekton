"""Wire contracts for run-scoped delivery."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from product_factory.domain.runs import WorkspaceProvenance


class DeliveryEntry(BaseModel):
    """One immutable blob and its safe landing suggestion."""

    model_config = {"extra": "forbid"}

    role: str
    logical_name: str
    blob_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    media_type: str = "application/octet-stream"
    kind: Literal["file", "patch"] = "file"
    suggested_dest_path: str | None = None
    changed_paths: list[str] = Field(default_factory=list)


class DeliveryManifest(BaseModel):
    """Immutable manifest fetched by a laptop before landing."""

    model_config = {"extra": "forbid"}

    schema_version: Literal["delivery_manifest.v1"] = "delivery_manifest.v1"
    delivery_id: str
    run_id: str
    base_revision: str
    workspace_provenance: WorkspaceProvenance | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entries: list[DeliveryEntry]
    manifest_sha256: str = ""


class LandingReceipt(BaseModel):
    """Client assertion appended by the server after a local landing."""

    model_config = {"extra": "forbid"}

    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_revision: str
    status: Literal["landed"]
    landed_paths: list[str]
    client: str = "product-factory"
