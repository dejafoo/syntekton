from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from product_factory.connectors import ops_read
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.errors import ConnectorPolicyDenied, ConnectorUnavailable
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorRegistry


def _broker(*, mock: bool = True, token: bool = False, **options: object) -> ConnectorBroker:
    registry = ConnectorRegistry()
    registry.register(ops_read.ops_read_manifest(), ops_read.ops_read)
    config = ConnectorsConfig(
        connectors={ops_read.CONNECTOR_ID: ConnectorSettings(enabled=True, options=dict(options))}
    )
    return ConnectorBroker(
        registry,
        config=config,
        environ={"OPS_READ_TOKEN": "test"} if token else {},
        mock=mock,
    )


def _invoke(broker: ConnectorBroker, **arguments: object) -> dict:
    return broker.invoke(
        tool_name=ops_read.TOOL_QUERY_SIGNALS,
        arguments={
            "service_id": "checkout",
            "environment": "staging",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T01:00:00Z",
            "query_template": "release_health",
            **arguments,
        },
        task_id="release",
        tool_call_id="ops-1",
    )


def test_mock_ops_query_is_bounded_and_hash_stable() -> None:
    broker = _broker(
        allowed_services=["checkout"],
        allowed_environments=["staging"],
        max_window_seconds=7200,
    )
    first = _invoke(broker)
    second = _invoke(broker)
    assert first["result"]["query_hash"] == second["result"]["query_hash"]
    assert first["result"]["stale"] is False


def test_ops_incident_query_preserves_hash_window_and_excerpt_retention() -> None:
    manifest = ops_read.ops_read_manifest()
    assert manifest.result_retention == "excerpt"
    broker = _broker(
        allowed_services=["checkout"],
        allowed_environments=["staging"],
        query_templates=["incident_context"],
    )
    result = broker.invoke(
        tool_name=ops_read.TOOL_GET_INCIDENT,
        arguments={
            "service_id": "checkout",
            "environment": "staging",
            "start": "2026-01-01T00:00:00Z",
            "end": "2026-01-01T01:00:00Z",
            "incident_id": "INC-42",
            "query_template": "incident_context",
        },
        task_id="incident",
        tool_call_id="ops-incident",
    )["result"]
    assert len(result["query_hash"]) == 64
    assert result["start"] == "2026-01-01T00:00:00+00:00"
    assert result["end"] == "2026-01-01T01:00:00+00:00"
    assert result["incident"]["id"] == "INC-42"


def test_ops_scope_cannot_widen_service_environment_window_or_template() -> None:
    broker = _broker(
        allowed_services=["checkout"],
        allowed_environments=["staging"],
        query_templates=["release_health"],
        max_window_seconds=3600,
    )
    with pytest.raises(ConnectorPolicyDenied, match="outside"):
        _invoke(broker, service_id="payments")
    with pytest.raises(ConnectorPolicyDenied, match="outside"):
        _invoke(broker, environment="production")
    with pytest.raises(ConnectorPolicyDenied, match="window"):
        _invoke(broker, end="2026-01-01T02:00:01Z")
    with pytest.raises(ConnectorPolicyDenied, match="template"):
        _invoke(broker, query_template="raw_query")


def test_ops_auth_loss_rate_limit_redaction_and_staleness() -> None:
    with pytest.raises(ConnectorUnavailable, match="OPS_READ_TOKEN"):
        _invoke(_broker(mock=False))

    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()

    def backend(**_: object) -> dict:
        return {
            "observed_at": old,
            "signals": [{"name": "message", "api_key": "must-not-survive"}],
        }

    result = _invoke(_broker(mock=False, token=True, backend=backend, stale_after_seconds=60))
    assert result["result"]["stale"] is True
    assert result["result"]["signals"][0]["api_key"] == "[REDACTED]"

    def limited(**_: object) -> dict:
        raise ConnectorUnavailable("rate limit reached", details={"status_code": 429})

    with pytest.raises(ConnectorUnavailable, match="rate limit"):
        _invoke(_broker(mock=False, token=True, backend=limited))
