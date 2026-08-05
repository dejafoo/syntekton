"""Bounded read-only service signals and incident connector."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from product_factory.connectors.errors import ConnectorPolicyDenied, ConnectorUnavailable
from product_factory.connectors.manifest import ConnectorManifest, ConnectorToolSpec, EgressPolicy
from product_factory.connectors.registry import ConnectorInvocation
from product_factory.connectors.result import ConnectorResult, Provenance

CONNECTOR_ID = "ops_read"
TOOL_CLASS = "ops_read"
TOOL_QUERY_SIGNALS = "query_service_signals"
TOOL_GET_INCIDENT = "get_incident"
API_HOST = "observability.example.invalid"
QUERY_TEMPLATES = frozenset({"release_health", "error_budget", "incident_context"})
DEFAULT_MAX_WINDOW_SECONDS = 24 * 60 * 60
DEFAULT_MAX_ROWS = 200


def _window_schema(*, incident: bool = False) -> dict[str, Any]:
    required = ["service_id", "environment", "start", "end"]
    if incident:
        required.append("incident_id")
    properties: dict[str, Any] = {
        "service_id": {"type": "string", "minLength": 1},
        "environment": {"type": "string", "minLength": 1},
        "start": {"type": "string", "format": "date-time"},
        "end": {"type": "string", "format": "date-time"},
        "query_template": {"type": "string", "enum": sorted(QUERY_TEMPLATES)},
        "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
    }
    if incident:
        properties["incident_id"] = {"type": "string", "minLength": 1}
    return {
        "type": "object",
        "required": required,
        "properties": properties,
        "additionalProperties": False,
    }


def ops_read_manifest() -> ConnectorManifest:
    return ConnectorManifest(
        connector_id=CONNECTOR_ID,
        version="1.0.0",
        provider="bounded-observability",
        tool_class=TOOL_CLASS,
        permissions=frozenset({"read"}),
        tools=(
            ConnectorToolSpec(
                name=TOOL_QUERY_SIGNALS,
                description="Run an allowlisted service/environment/time-window signal query",
                input_schema=_window_schema(),
                risk_class="R2",
            ),
            ConnectorToolSpec(
                name=TOOL_GET_INCIDENT,
                description="Read one incident within a bounded service and time window",
                input_schema=_window_schema(incident=True),
                risk_class="R2",
            ),
        ),
        egress=EgressPolicy(mode="domains", allowed_domains=(API_HOST,)),
        auth_env_var="OPS_READ_TOKEN",
        timeout_seconds=20,
        max_concurrency=2,
        result_retention="excerpt",
        max_result_bytes=64_000,
        description="Bounded observability and incident read plane",
    )


def _parse_time(raw: Any, name: str) -> datetime:
    try:
        value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConnectorPolicyDenied(
            f"ops_read {name} must be an ISO-8601 timestamp",
            connector_id=CONNECTOR_ID,
            details={name: str(raw)},
        ) from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded_scope(invocation: ConnectorInvocation) -> dict[str, Any]:
    args = invocation.arguments
    service = str(args.get("service_id") or "").strip()
    environment = str(args.get("environment") or "").strip()
    start = _parse_time(args.get("start"), "start")
    end = _parse_time(args.get("end"), "end")
    maximum = int(invocation.options.get("max_window_seconds", DEFAULT_MAX_WINDOW_SECONDS))
    if end <= start or (end - start).total_seconds() > maximum:
        raise ConnectorPolicyDenied(
            "ops_read time window is empty, reversed, or exceeds policy",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
            details={"max_window_seconds": maximum},
        )
    for key, value, option in (
        ("service_id", service, "allowed_services"),
        ("environment", environment, "allowed_environments"),
    ):
        allowed = {
            str(item).strip() for item in invocation.options.get(option, ()) if str(item).strip()
        }
        if allowed and value not in allowed:
            raise ConnectorPolicyDenied(
                f"{key} {value!r} is outside the ops_read scope",
                connector_id=CONNECTOR_ID,
                tool_name=invocation.tool_name,
                details={option: sorted(allowed)},
            )
    template = str(args.get("query_template") or "release_health").strip()
    configured_templates = frozenset(
        str(item).strip()
        for item in invocation.options.get("query_templates", QUERY_TEMPLATES)
        if str(item).strip()
    )
    if template not in QUERY_TEMPLATES or template not in configured_templates:
        raise ConnectorPolicyDenied(
            f"Unknown or disabled ops query template {template!r}",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
        )
    max_rows = min(
        max(1, int(args.get("max_rows") or DEFAULT_MAX_ROWS)),
        max(1, int(invocation.options.get("max_rows", DEFAULT_MAX_ROWS))),
    )
    scope = {
        "service_id": service,
        "environment": environment,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "query_template": template,
        "max_rows": max_rows,
    }
    scope["query_hash"] = hashlib.sha256(
        json.dumps(scope, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return scope


def _redact(value: Any) -> Any:
    sensitive = {"authorization", "token", "api_key", "password", "secret"}
    if isinstance(value, dict):
        return {
            str(key): ("[REDACTED]" if str(key).lower() in sensitive else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _mock_result(invocation: ConnectorInvocation, scope: dict[str, Any]) -> ConnectorResult:
    if invocation.tool_name == TOOL_GET_INCIDENT:
        data: dict[str, Any] = {
            "incident": {
                "id": str(invocation.arguments.get("incident_id") or ""),
                "status": "resolved",
                "summary": "Hermetic release-health fixture",
            }
        }
    else:
        data = {
            "signals": [
                {"name": "error_rate", "value": 0.002, "unit": "ratio"},
                {"name": "p95_latency_ms", "value": 145, "unit": "ms"},
            ]
        }
    payload = {
        **scope,
        **data,
        "observed_at": "2026-01-01T00:30:00+00:00",
        "stale": False,
    }
    return ConnectorResult(
        payload=payload,
        provenance=(
            Provenance(
                source=f"fixture://ops/{scope['service_id']}/{scope['query_hash']}",
                kind="fixture",
            ),
        ),
        metadata={"mock": True, "query_hash": scope["query_hash"]},
    )


def ops_read(invocation: ConnectorInvocation) -> ConnectorResult:
    scope = _bounded_scope(invocation)
    if invocation.mock:
        return _mock_result(invocation, scope)

    invocation.assert_egress_allowed(f"https://{API_HOST}")
    backend = invocation.options.get("backend")
    if backend is None:
        raise ConnectorUnavailable(
            "ops_read has no configured backend",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
        )
    try:
        raw = backend(
            tool_name=invocation.tool_name,
            scope=dict(scope),
            incident_id=invocation.arguments.get("incident_id"),
            token=invocation.secret,
            timeout_seconds=invocation.timeout_seconds,
        )
    except ConnectorUnavailable:
        raise
    except Exception as exc:
        raise ConnectorUnavailable(
            f"Ops read backend failed: {type(exc).__name__}",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
            details={"error_type": type(exc).__name__},
        ) from exc
    if not isinstance(raw, dict):
        raise ConnectorUnavailable(
            "Ops read backend returned a non-object",
            connector_id=CONNECTOR_ID,
            tool_name=invocation.tool_name,
        )
    payload = _redact(raw)
    # Scope fields are trusted policy output, never provider-controlled.
    payload.update(scope)
    observed_at = _parse_time(
        payload.get("observed_at") or datetime.now(UTC).isoformat(), "observed_at"
    )
    stale_after = int(invocation.options.get("stale_after_seconds", 15 * 60))
    payload["observed_at"] = observed_at.isoformat()
    payload["stale"] = datetime.now(UTC) - observed_at > timedelta(seconds=stale_after)
    return ConnectorResult(
        payload=payload,
        provenance=(
            Provenance(
                source=f"ops://{scope['service_id']}/{scope['query_hash']}",
                kind="observability",
            ),
        ),
        metadata={"query_hash": scope["query_hash"], "stale": payload["stale"]},
    )


__all__ = [
    "API_HOST",
    "CONNECTOR_ID",
    "QUERY_TEMPLATES",
    "TOOL_CLASS",
    "TOOL_GET_INCIDENT",
    "TOOL_QUERY_SIGNALS",
    "ops_read",
    "ops_read_manifest",
]
