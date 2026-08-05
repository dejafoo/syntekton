from __future__ import annotations

import pytest

from product_factory.connectors import git_ci
from product_factory.connectors.broker import ConnectorBroker
from product_factory.connectors.errors import ConnectorPolicyDenied, ConnectorUnavailable
from product_factory.connectors.policy import ConnectorsConfig, ConnectorSettings
from product_factory.connectors.registry import ConnectorRegistry


def _broker(*, mock: bool = True, token: bool = False, **options: object) -> ConnectorBroker:
    registry = ConnectorRegistry()
    registry.register(git_ci.git_ci_manifest(), git_ci.git_ci_read)
    config = ConnectorsConfig(
        connectors={git_ci.CONNECTOR_ID: ConnectorSettings(enabled=True, options=dict(options))}
    )
    return ConnectorBroker(
        registry,
        config=config,
        environ={"GITHUB_TOKEN": "test"} if token else {},
        mock=mock,
    )


def _invoke(broker: ConnectorBroker, **arguments: object) -> dict:
    return broker.invoke(
        tool_name=git_ci.TOOL_GET_CHECKS,
        arguments={
            "repository": "acme/service",
            "commit_sha": "a" * 40,
            **arguments,
        },
        task_id="release",
        tool_call_id="ci-1",
    )


def test_mock_git_ci_is_deterministic_and_pinned_to_sha() -> None:
    broker = _broker(allowed_repositories=["acme/service"])
    first = _invoke(broker)
    second = _invoke(broker)
    assert first["result"] == second["result"]
    assert first["result"]["commit_sha"] == "a" * 40


def test_git_ci_rejects_mutable_revision_and_repository_scope_widening() -> None:
    broker = _broker(allowed_repositories=["acme/service"])
    with pytest.raises(ConnectorPolicyDenied, match="immutable"):
        _invoke(broker, commit_sha="main")
    with pytest.raises(ConnectorPolicyDenied, match="outside"):
        _invoke(broker, repository="other/service")


def test_live_git_ci_requires_auth_and_types_rate_limit() -> None:
    with pytest.raises(ConnectorUnavailable, match="GITHUB_TOKEN"):
        _invoke(_broker(mock=False))

    def limited(**_: object) -> dict:
        raise ConnectorUnavailable("rate limit reached", details={"status_code": 429})

    with pytest.raises(ConnectorUnavailable, match="rate limit"):
        _invoke(_broker(mock=False, token=True, backend=limited))


def test_git_ci_provider_cannot_replace_pinned_scope() -> None:
    def hostile(**_: object) -> dict:
        return {
            "repository": "attacker/widened",
            "commit_sha": "b" * 40,
            "checks": [{"name": "test", "conclusion": "success"}],
        }

    result = _invoke(_broker(mock=False, token=True, backend=hostile))
    assert result["result"]["repository"] == "acme/service"
    assert result["result"]["commit_sha"] == "a" * 40


def test_git_ci_oversize_result_is_visibly_truncated() -> None:
    def oversized(**_: object) -> dict:
        return {"checks": [{"name": "huge", "text": "x" * 100_000}]}

    result = _invoke(_broker(mock=False, token=True, backend=oversized))
    assert result["truncated"] is True
