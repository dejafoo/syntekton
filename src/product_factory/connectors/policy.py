"""Operator policy for connectors — the allowlist that turns a manifest on.

A registered manifest describes what a connector *could* do. This config decides
whether the operator lets it, and can only ever narrow the manifest: tighter
egress, fewer results, a shorter timeout, an approval requirement the manifest
did not ask for. Config cannot widen permissions or add tools.

Everything is off by default. An unconfigured connector is denied, so shipping a
new connector never silently enables egress on an existing install.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from product_factory.connectors.errors import ConnectorPolicyDenied
from product_factory.connectors.manifest import (
    ConnectorManifest,
    EgressPolicy,
    domain_matches,
    host_of,
)
from product_factory.domain.errors import ConfigurationError


class ConnectorSettings(BaseModel):
    """Per-connector operator settings."""

    model_config = {"frozen": True}

    enabled: bool = False
    # Narrows the manifest's egress allowlist by intersection; empty means "as declared".
    allowed_domains: tuple[str, ...] = ()
    denied_domains: tuple[str, ...] = ()
    # Cap the manifest timeout; never raises it.
    max_timeout_seconds: int | None = None
    # Force operator approval even when the manifest does not require it.
    require_approval: bool = False
    max_result_bytes: int | None = None
    # Handler-specific knobs (max_results, root paths, …). Passed through
    # verbatim to the handler as `ConnectorInvocation.options`.
    options: dict[str, Any] = Field(default_factory=dict)


class ConnectorsConfig(BaseModel):
    """Contents of `config/connectors.yaml`."""

    model_config = {"frozen": True}

    # Write-capable connectors are out of scope for Phase 4; the flag exists so
    # enabling one later is an explicit, reviewable config change.
    allow_write_connectors: bool = False
    connectors: dict[str, ConnectorSettings] = Field(default_factory=dict)

    def settings_for(self, connector_id: str) -> ConnectorSettings:
        direct = self.connectors.get(connector_id)
        if direct is not None:
            return direct
        # SD7: simulated_staging supersedes staging_deploy; accept either config key.
        if connector_id == "simulated_staging":
            return self.connectors.get("staging_deploy", ConnectorSettings())
        if connector_id == "staging_deploy":
            return self.connectors.get("simulated_staging", ConnectorSettings())
        return ConnectorSettings()

    def is_enabled(self, connector_id: str) -> bool:
        return self.settings_for(connector_id).enabled

    def enabled_ids(self) -> tuple[str, ...]:
        return tuple(sorted(cid for cid, settings in self.connectors.items() if settings.enabled))

    def effective_egress(self, manifest: ConnectorManifest) -> EgressPolicy:
        """Manifest egress narrowed by operator config."""
        settings = self.settings_for(manifest.connector_id)
        declared = manifest.egress
        denied = tuple(dict.fromkeys((*declared.denied_domains, *settings.denied_domains)))
        if not settings.allowed_domains:
            return declared.model_copy(update={"denied_domains": denied})
        if declared.mode == "none":
            # Config cannot grant egress a manifest never declared.
            raise ConnectorPolicyDenied(
                f"Connector {manifest.connector_id!r} declares no egress; config "
                "cannot add allowed domains",
                connector_id=manifest.connector_id,
                details={"config_allowed_domains": list(settings.allowed_domains)},
            )
        narrowed = tuple(
            domain
            for domain in settings.allowed_domains
            if any(domain_matches(host_of(domain), pattern) for pattern in declared.allowed_domains)
        )
        if not narrowed:
            raise ConnectorPolicyDenied(
                f"Connector {manifest.connector_id!r} config allows no domain the "
                "manifest declares",
                connector_id=manifest.connector_id,
                details={
                    "config_allowed_domains": list(settings.allowed_domains),
                    "manifest_allowed_domains": list(declared.allowed_domains),
                },
            )
        return EgressPolicy(mode="domains", allowed_domains=narrowed, denied_domains=denied)

    def effective_timeout(self, manifest: ConnectorManifest, declared: int) -> int:
        cap = self.settings_for(manifest.connector_id).max_timeout_seconds
        return min(declared, cap) if cap else declared

    def effective_max_result_bytes(self, manifest: ConnectorManifest) -> int:
        cap = self.settings_for(manifest.connector_id).max_result_bytes
        return min(manifest.max_result_bytes, cap) if cap else manifest.max_result_bytes

    def requires_approval(self, manifest: ConnectorManifest) -> bool:
        return (
            manifest.requires_approval or self.settings_for(manifest.connector_id).require_approval
        )


def load_connectors_config(config_dir: Path) -> ConnectorsConfig:
    """Read `connectors.yaml`. A missing file means no connectors are enabled."""
    path = config_dir / "connectors.yaml"
    if not path.exists():
        return ConnectorsConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Config root must be a mapping: {path}")
    try:
        return ConnectorsConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigurationError(f"Invalid connector config in {path}: {exc}") from exc


__all__ = ["ConnectorSettings", "ConnectorsConfig", "load_connectors_config"]
