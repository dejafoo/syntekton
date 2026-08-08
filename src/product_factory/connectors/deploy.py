"""Hermetic simulated staging deployment connector and durable fixture adapter.

This is an in-process simulator for change-control tests. It is not a production
deployment integration. Prefer the ``simulated_staging`` connector id and
``simulated-*`` target ids; legacy ``staging_deploy`` / ``staging-local`` names
remain as compatibility aliases during SD7.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from product_factory.connectors.errors import (
    ConnectorPolicyDenied,
    ConnectorTimeout,
    ConnectorUnavailable,
)
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec, EgressPolicy
from product_factory.connectors.registry import ConnectorInvocation
from product_factory.connectors.result import ConnectorResult, Provenance
from product_factory.domain.errors import ConfigurationError

CONNECTOR_ID = "simulated_staging"
LEGACY_CONNECTOR_ID = "staging_deploy"
CONNECTOR_ID_ALIASES: frozenset[str] = frozenset({CONNECTOR_ID, LEGACY_CONNECTOR_ID})
TOOL_CLASS = "deployment_write"
TOOL_RESOLVE = "resolve_deployment_target"
TOOL_START = "start_deployment"
TOOL_STATUS = "get_rollout_status"
TOOL_HEALTH = "verify_health"
TOOL_ROLLBACK = "rollback_deployment"
TOOLS = (TOOL_RESOLVE, TOOL_START, TOOL_STATUS, TOOL_HEALTH, TOOL_ROLLBACK)

# Target id aliases: simulated names are preferred; staging-* remains readable.
TARGET_ID_ALIASES: dict[str, str] = {
    "staging-local": "simulated-local",
    "staging-live": "simulated-restart",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DeploymentTarget(BaseModel):
    """Operator-declared deployment destination."""

    model_config = {"frozen": True, "extra": "forbid"}

    target_id: str
    environment: str
    adapter: str = "in_process"
    enabled: bool = True
    health_checks: tuple[str, ...] = ("rollout",)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_unsafe_environment(self) -> DeploymentTarget:
        if self.environment.strip().lower() in {"prod", "production"}:
            raise ValueError("production deployment targets are disabled")
        if not self.target_id.strip():
            raise ValueError("target_id must not be empty")
        return self


class DeploymentTargetsConfig(BaseModel):
    model_config = {"frozen": True, "extra": "forbid"}

    targets: tuple[DeploymentTarget, ...] = ()

    @model_validator(mode="after")
    def unique_ids(self) -> DeploymentTargetsConfig:
        ids = [target.target_id for target in self.targets]
        duplicates = sorted({target_id for target_id in ids if ids.count(target_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate deployment target ids: {duplicates}")
        return self


class DeploymentTargetRegistry:
    """Immutable allowlist. Unknown, disabled, and production targets fail closed."""

    def __init__(self, targets: tuple[DeploymentTarget, ...] | list[DeploymentTarget] = ()) -> None:
        self._targets = {target.target_id: target for target in targets}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> DeploymentTargetRegistry:
        try:
            config = DeploymentTargetsConfig.model_validate(raw)
        except Exception as exc:
            raise ConfigurationError(f"Invalid deployment target config: {exc}") from exc
        return cls(config.targets)

    @classmethod
    def from_file(cls, path: Path) -> DeploymentTargetRegistry:
        if not path.exists():
            return cls()
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigurationError(f"Deployment target config root must be a mapping: {path}")
        return cls.from_mapping(raw)

    def resolve(self, target_id: str) -> DeploymentTarget:
        requested = (target_id or "").strip()
        canonical = TARGET_ID_ALIASES.get(requested, requested)
        target = self._targets.get(canonical) or self._targets.get(requested)
        if target is None or not target.enabled:
            raise ConnectorPolicyDenied(
                f"Deployment target {target_id!r} is not enabled and allowlisted",
                connector_id=CONNECTOR_ID,
                tool_name=TOOL_RESOLVE,
                details={"target_id": target_id},
            )
        # Defense in depth for registries assembled by non-Pydantic callers.
        if target.environment.lower() in {"prod", "production"}:
            raise ConnectorPolicyDenied(
                "Production deployment is not supported",
                connector_id=CONNECTOR_ID,
                tool_name=TOOL_RESOLVE,
                details={"target_id": target_id, "environment": target.environment},
            )
        return target

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._targets))


def load_deployment_targets_config(config_dir: Path) -> DeploymentTargetsConfig:
    """Load the optional target allowlist; missing means no deployment targets."""
    path = config_dir / "deployment_targets.yaml"
    if not path.exists():
        return DeploymentTargetsConfig()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Deployment target config root must be a mapping: {path}")
    try:
        return DeploymentTargetsConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigurationError(f"Invalid deployment target config in {path}: {exc}") from exc


@dataclass(frozen=True)
class DeploymentReceipt:
    action: str
    status: str
    deployment_id: str | None
    target_id: str
    idempotency_key: str
    observed_at: str
    details: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "deployment_id": self.deployment_id,
            "target_id": self.target_id,
            "idempotency_key": self.idempotency_key,
            "observed_at": self.observed_at,
            **self.details,
        }


class SimulatedStagingAdapter:
    """Thread-safe simulated staging control plane with durable idempotency receipts."""

    def __init__(
        self,
        registry: DeploymentTargetRegistry,
        *,
        state_path: Path | None = None,
    ) -> None:
        self.registry = registry
        self.state_path = state_path
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"deployments": {}, "idempotency": {}, "target_locks": {}}
        self._load()

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConnectorUnavailable(
                "Deployment state cannot be reconciled",
                connector_id=CONNECTOR_ID,
                details={"state_path": str(self.state_path), "error_type": type(exc).__name__},
            ) from exc
        if isinstance(loaded, dict):
            self._state.update(loaded)

    def _save(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(f"{self.state_path.suffix}.tmp")
        temporary.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(self.state_path)

    def resolve_target(self, target_id: str) -> dict[str, Any]:
        target = self.registry.resolve(target_id)
        return {
            "target_id": target.target_id,
            "environment": target.environment,
            "adapter": target.adapter,
            "health_checks": list(target.health_checks),
            "allowed": True,
        }

    def start(
        self,
        *,
        target_id: str,
        release_plan_digest: str,
        artifact_digest: str,
        idempotency_key: str,
        change_window: Mapping[str, Any],
        approved: bool,
    ) -> DeploymentReceipt:
        if not approved:
            raise ConnectorPolicyDenied(
                "Deployment effect requires trusted operator approval",
                connector_id=CONNECTOR_ID,
                tool_name=TOOL_START,
                details={"target_id": target_id},
            )
        target = self.registry.resolve(target_id)
        for name, digest in (
            ("release_plan_digest", release_plan_digest),
            ("artifact_digest", artifact_digest),
        ):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest.lower()):
                raise ConnectorPolicyDenied(
                    f"{name} must be an immutable sha256 digest",
                    connector_id=CONNECTOR_ID,
                    tool_name=TOOL_START,
                    details={"field": name},
                )
        if not idempotency_key.strip():
            raise ConnectorPolicyDenied(
                "start_deployment requires an idempotency key",
                connector_id=CONNECTOR_ID,
                tool_name=TOOL_START,
            )
        binding = {
            "target_id": target.target_id,
            "release_plan_digest": release_plan_digest.lower(),
            "artifact_digest": artifact_digest.lower(),
            "change_window": dict(change_window),
        }
        with self._lock:
            prior_id = self._state["idempotency"].get(idempotency_key)
            if prior_id:
                prior = self._state["deployments"][prior_id]
                if prior["binding"] != binding:
                    raise ConnectorPolicyDenied(
                        "Idempotency key is already bound to different immutable inputs",
                        connector_id=CONNECTOR_ID,
                        tool_name=TOOL_START,
                        details={"idempotency_key": idempotency_key},
                    )
                return self._receipt(TOOL_START, prior, replayed=True)
            lock_owner = self._state["target_locks"].get(target.target_id)
            if lock_owner:
                raise ConnectorPolicyDenied(
                    f"Deployment target {target.target_id!r} already has an active rollout",
                    connector_id=CONNECTOR_ID,
                    tool_name=TOOL_START,
                    details={"deployment_id": lock_owner},
                )
            deployment_id = f"dep-{idempotency_key}"
            record = {
                "deployment_id": deployment_id,
                "target_id": target.target_id,
                "environment": target.environment,
                "idempotency_key": idempotency_key,
                "binding": binding,
                "status": "started",
                "health": None,
                "rollback_result": None,
                "action_log": [
                    {
                        "action": TOOL_START,
                        "status": "started",
                        "observed_at": _now(),
                        "confirmed": True,
                    }
                ],
            }
            self._state["deployments"][deployment_id] = record
            self._state["idempotency"][idempotency_key] = deployment_id
            self._state["target_locks"][target.target_id] = deployment_id
            self._save()
            return self._receipt(TOOL_START, record)

    def status(self, deployment_id: str) -> DeploymentReceipt:
        with self._lock:
            record = self._record(deployment_id)
            return self._receipt(TOOL_STATUS, record)

    def verify_health(
        self,
        deployment_id: str,
        *,
        healthy: bool | None = None,
        checks: list[dict[str, Any]] | None = None,
    ) -> DeploymentReceipt:
        with self._lock:
            record = self._record(deployment_id)
            passed = True if healthy is None else bool(healthy)
            record["health"] = {
                "healthy": passed,
                "checks": checks or [{"name": "rollout", "passed": passed}],
                "observed_at": _now(),
            }
            record["status"] = "succeeded" if passed else "halted"
            record["action_log"].append(
                {"action": TOOL_HEALTH, "status": record["status"], "observed_at": _now()}
            )
            if passed:
                self._state["target_locks"].pop(record["target_id"], None)
            self._save()
            return self._receipt(TOOL_HEALTH, record)

    def rollback(self, deployment_id: str, *, approved: bool) -> DeploymentReceipt:
        if not approved:
            raise ConnectorPolicyDenied(
                "Rollback effect requires trusted operator approval",
                connector_id=CONNECTOR_ID,
                tool_name=TOOL_ROLLBACK,
            )
        with self._lock:
            record = self._record(deployment_id)
            rollback_result = record.get("rollback_result")
            if isinstance(rollback_result, dict) and rollback_result.get("status") == "rolled_back":
                return self._receipt(TOOL_ROLLBACK, record, replayed=True)
            record["status"] = "rolled_back"
            record["rollback_result"] = {"status": "rolled_back", "observed_at": _now()}
            record["action_log"].append(
                {"action": TOOL_ROLLBACK, "status": "rolled_back", "observed_at": _now()}
            )
            self._state["target_locks"].pop(record["target_id"], None)
            self._save()
            return self._receipt(TOOL_ROLLBACK, record)

    def timeout_receipt(self, *, target_id: str, idempotency_key: str) -> DeploymentReceipt:
        return DeploymentReceipt(
            action=TOOL_STATUS,
            status="unknown",
            deployment_id=None,
            target_id=target_id,
            idempotency_key=idempotency_key,
            observed_at=_now(),
            details={"durable": True, "reason": "timeout", "reconciliation_required": True},
        )

    def _record(self, deployment_id: str) -> dict[str, Any]:
        record = self._state["deployments"].get(deployment_id)
        if record is None:
            raise ConnectorUnavailable(
                f"Unknown deployment {deployment_id!r}",
                connector_id=CONNECTOR_ID,
                details={"deployment_id": deployment_id},
            )
        return record

    def _receipt(
        self, action: str, record: dict[str, Any], *, replayed: bool = False
    ) -> DeploymentReceipt:
        return DeploymentReceipt(
            action=action,
            status=str(record["status"]),
            deployment_id=str(record["deployment_id"]),
            target_id=str(record["target_id"]),
            idempotency_key=str(record["idempotency_key"]),
            observed_at=_now(),
            details={
                "environment": record["environment"],
                "binding": dict(record["binding"]),
                "health": record.get("health"),
                "rollback_result": record.get("rollback_result"),
                "action_log": list(record["action_log"]),
                "replayed": replayed,
                "durable": self.state_path is not None,
            },
        )


def _tool(name: str, *, write: bool = False) -> ConnectorToolSpec:
    schemas: dict[str, dict[str, Any]] = {
        TOOL_RESOLVE: {
            "type": "object",
            "required": ["target_id"],
            "properties": {"target_id": {"type": "string"}},
            "additionalProperties": False,
        },
        TOOL_START: {
            "type": "object",
            "required": [
                "target_id",
                "release_plan_digest",
                "artifact_digest",
                "idempotency_key",
                "change_window",
            ],
            "properties": {
                "target_id": {"type": "string"},
                "release_plan_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "artifact_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "idempotency_key": {"type": "string", "minLength": 1},
                "change_window": {"type": "object"},
            },
            "additionalProperties": False,
        },
        TOOL_STATUS: {
            "type": "object",
            "required": ["deployment_id"],
            "properties": {"deployment_id": {"type": "string"}},
            "additionalProperties": False,
        },
        TOOL_HEALTH: {
            "type": "object",
            "required": ["deployment_id"],
            "properties": {
                "deployment_id": {"type": "string"},
                "healthy": {"type": "boolean"},
                "checks": {"type": "array"},
            },
            "additionalProperties": False,
        },
        TOOL_ROLLBACK: {
            "type": "object",
            "required": ["deployment_id"],
            "properties": {"deployment_id": {"type": "string"}},
            "additionalProperties": False,
        },
    }
    return ConnectorToolSpec(
        name=name,
        description=f"Simulated staging deployment fixture action: {name}",
        input_schema=schemas[name],
        permissions=frozenset({"write"}) if write else frozenset({"read"}),
        risk_class="R3" if write else "R2",
        requires_approval=write,
        idempotent=True,
    )


def simulated_staging_manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        provider="product-factory-simulated-staging",
        tool_class=TOOL_CLASS,
        tools=(
            _tool(TOOL_RESOLVE),
            _tool(TOOL_START, write=True),
            _tool(TOOL_STATUS),
            _tool(TOOL_HEALTH),
            _tool(TOOL_ROLLBACK, write=True),
        ),
        permissions=frozenset({"read", "write"}),
        egress=EgressPolicy(mode="none"),
        timeout_seconds=30,
        max_concurrency=4,
        result_retention="full",
        description="In-process simulated staging fixture (not production deploy)",
    )


class SimulatedStagingHandler:
    def __init__(self, adapter: SimulatedStagingAdapter) -> None:
        self.adapter = adapter

    def __call__(self, invocation: ConnectorInvocation) -> ConnectorResult:
        args = invocation.arguments
        approved = bool(invocation.options.get("_connector_approved"))
        try:
            if invocation.tool_name == TOOL_RESOLVE:
                payload = self.adapter.resolve_target(str(args.get("target_id") or ""))
            elif invocation.tool_name == TOOL_START:
                payload = self.adapter.start(
                    target_id=str(args.get("target_id") or ""),
                    release_plan_digest=str(args.get("release_plan_digest") or ""),
                    artifact_digest=str(args.get("artifact_digest") or ""),
                    idempotency_key=str(args.get("idempotency_key") or ""),
                    change_window=dict(args.get("change_window") or {}),
                    approved=approved,
                ).as_payload()
            elif invocation.tool_name == TOOL_STATUS:
                payload = self.adapter.status(str(args.get("deployment_id") or "")).as_payload()
            elif invocation.tool_name == TOOL_HEALTH:
                payload = self.adapter.verify_health(
                    str(args.get("deployment_id") or ""),
                    healthy=args.get("healthy"),
                    checks=list(args.get("checks") or []),
                ).as_payload()
            elif invocation.tool_name == TOOL_ROLLBACK:
                payload = self.adapter.rollback(
                    str(args.get("deployment_id") or ""), approved=approved
                ).as_payload()
            else:
                raise ConnectorPolicyDenied(
                    f"Unknown deployment tool {invocation.tool_name!r}",
                    connector_id=CONNECTOR_ID,
                    tool_name=invocation.tool_name,
                )
        except ConnectorTimeout:
            payload = self.adapter.timeout_receipt(
                target_id=str(args.get("target_id") or ""),
                idempotency_key=str(args.get("idempotency_key") or ""),
            ).as_payload()
        return ConnectorResult(
            payload=payload,
            provenance=(
                Provenance(
                    source=f"deployment://{payload.get('target_id', 'simulated')}",
                    kind="deployment_receipt",
                ),
            ),
        )


def default_simulated_staging_adapter(
    *,
    config_root: Path | None = None,
    state_path: Path | None = None,
) -> SimulatedStagingAdapter:
    root = config_root or Path.cwd()
    registry = DeploymentTargetRegistry.from_file(root / "config" / "deployment_targets.yaml")
    return SimulatedStagingAdapter(registry, state_path=state_path)


def simulated_deploy_integration_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Opt-in hermetic simulator smoke (DEPLOY_INTEGRATION=1). Not a live deploy."""
    env = environ if environ is not None else os.environ
    return str(env.get("DEPLOY_INTEGRATION") or "").strip() == "1"


# Compatibility aliases (SD7 rename). Prefer simulated_* names in new code.
StagingDeployAdapter = SimulatedStagingAdapter
StagingDeployHandler = SimulatedStagingHandler
staging_deploy_manifest = simulated_staging_manifest
default_staging_adapter = default_simulated_staging_adapter
live_deploy_enabled = simulated_deploy_integration_enabled


__all__ = [
    "CONNECTOR_ID",
    "CONNECTOR_ID_ALIASES",
    "LEGACY_CONNECTOR_ID",
    "DeploymentReceipt",
    "DeploymentTarget",
    "DeploymentTargetRegistry",
    "DeploymentTargetsConfig",
    "SimulatedStagingAdapter",
    "SimulatedStagingHandler",
    "StagingDeployAdapter",
    "StagingDeployHandler",
    "TOOLS",
    "TOOL_HEALTH",
    "TOOL_RESOLVE",
    "TOOL_ROLLBACK",
    "TOOL_START",
    "TOOL_STATUS",
    "default_simulated_staging_adapter",
    "default_staging_adapter",
    "live_deploy_enabled",
    "load_deployment_targets_config",
    "simulated_deploy_integration_enabled",
    "simulated_staging_manifest",
    "staging_deploy_manifest",
]
